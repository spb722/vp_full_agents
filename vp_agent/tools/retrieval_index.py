from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from vp_agent.data import load_kpi_meta, load_vp_descriptions
from vp_agent.schemas import KpiMeta
from vp_agent.text import token_counter, tokens


SYNONYMS = {
    "topup": ["recharge", "denomination"],
    "top": ["recharge"],
    "up": ["recharge"],
    "smartphone": ["handset", "device", "sp"],
    "smartphones": ["handset", "device", "sp"],
    "omani": ["nationality"],
    "nationals": ["nationality"],
    "national": ["nationality"],
    "internet": ["data", "usage", "volume"],
    "spend": ["revenue", "amount"],
    "spent": ["revenue", "amount"],
    "active": ["status", "activity"],
    "pack": ["bundle", "product", "subscription"],
    # The client writes a campaign's control-group twin by suffixing the action
    # key with `_CG`, so the only token in the data is "cg". A marketer says
    # "control group", and nothing bridged the two: a lookup for
    # "control group variant" returned streaming-session counts while the four
    # rules that define the pattern went unseen.
    "control": ["cg"],
    "cg": ["control", "group"],
    "controlgroup": ["cg", "control"],
}


COLUMN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:_\$\{X\})?[A-Za-z0-9_]*\b")


def expand_tokens(raw_tokens: Iterable[str]) -> list[str]:
    expanded: list[str] = []
    for token in raw_tokens:
        expanded.append(token)
        expanded.extend(SYNONYMS.get(token, []))
    return expanded


def char_ngrams(text: str, n: int = 3) -> Counter[str]:
    """Local embedding surrogate.

    This vector is intentionally behind the same cosine-similarity contract a
    real embedding backend would expose. Replacing it with sentence or API
    embeddings should not change retrieval weighting.
    """
    normalized = " ".join(tokens(text))
    if not normalized:
        return Counter()
    padded = f"  {normalized}  "
    return Counter(padded[i : i + n] for i in range(max(0, len(padded) - n + 1)))


def cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass(frozen=True)
class RetrievalDocument:
    row: KpiMeta
    text: str
    term_counts: Counter[str]
    semantic_vector: Counter[str]
    length: int


@dataclass(frozen=True)
class RetrievalIndex:
    documents: tuple[RetrievalDocument, ...]
    doc_freq: dict[str, int]
    avgdl: float

    def bm25(self, query_terms: list[str], doc: RetrievalDocument, k1: float = 1.4, b: float = 0.72) -> float:
        score = 0.0
        total_docs = len(self.documents)
        for term in query_terms:
            tf = doc.term_counts.get(term, 0)
            if not tf:
                continue
            df = self.doc_freq.get(term, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * doc.length / max(self.avgdl, 1.0))
            score += idf * (tf * (k1 + 1)) / denom
        return score


EQUALITY_PREDICATE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s*(?:=|!=|<>)\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z][A-Za-z0-9_ -]*?))"
    r"(?=\s+AND\b|\s+OR\b|\s*\)|\s*$)",
    re.I,
)
IN_LIST_PREDICATE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\s+(?:NOT\s+)?IN\s+LIST\s*\(([^)]*)\)", re.I
)
NON_VALUE_TOKENS = {"null", "currenttime", "currentweek", "currentmonth", "days", "weeks", "months"}


def _candidate_values(condition: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for match in EQUALITY_PREDICATE_RE.finditer(condition):
        column = match.group(1)
        value = next((group for group in match.groups()[1:] if group), "")
        pairs.append((column, value))
    for match in IN_LIST_PREDICATE_RE.finditer(condition):
        column = match.group(1)
        for member in match.group(2).split(";"):
            pairs.append((column, member.strip().strip("'\"")))
    return pairs


def _usable_value_tokens(value: str) -> list[str]:
    """Real category words: no numbers, date fragments, or engine keywords."""
    return [
        token
        for token in tokens(value)
        if not token[0].isdigit() and token not in NON_VALUE_TOKENS and len(token) >= 2
    ]


@lru_cache(maxsize=1)
def column_observed_values() -> dict[str, tuple[str, ...]]:
    """Literal values each column has been compared against, casing preserved.

    `kpi_meta.value_references` is sparse — `Profile_Cdr_Nationality` records
    "NONE" and the handset column omits iPhone — so a categorical column's legal
    values are often invisible. Production rules already carry them, including
    their exact spelling: `LC_ACTION_TYPE` is stored with inconsistent casing,
    which is why every production rule matches it as a membership list rather
    than a single equality.
    """
    known = {row.feature_name for row in load_kpi_meta()}
    conditions: list[str] = []
    for client in ("omantel", "airtel"):
        try:
            conditions.extend(str(row.get("PARENT_CONDITION") or "") for row in load_vp_descriptions(client))
        except (OSError, ValueError):
            continue
    try:
        from vp_agent.golden import DEFAULT_GOLDEN_PATH, load_golden_cases

        conditions.extend(str(row.get("Expected Output") or "") for row in load_golden_cases(DEFAULT_GOLDEN_PATH))
    except (OSError, ValueError, ImportError):
        pass

    observed: defaultdict[str, set[str]] = defaultdict(set)
    for condition in conditions:
        for column, value in _candidate_values(condition):
            text = value.strip()
            if column not in known or not _usable_value_tokens(text):
                continue
            observed[column].add(text)
    return {column: tuple(sorted(values)) for column, values in observed.items()}


@lru_cache(maxsize=1)
def column_value_vocabulary() -> dict[str, tuple[str, ...]]:
    """Lowercased search tokens for those values, so retrieval can match them.

    Without this a marketer word like "Indian" has no lexical path to
    `Profile_Cdr_Nationality` at all.
    """
    vocabulary: defaultdict[str, set[str]] = defaultdict(set)
    for column, values in column_observed_values().items():
        for value in values:
            vocabulary[column].update(_usable_value_tokens(value))
    return {column: tuple(sorted(values)) for column, values in vocabulary.items()}


def document_text(row: KpiMeta, observed_values: tuple[str, ...] = ()) -> str:
    return " ".join(
        [
            row.feature_name.replace("_", " "),
            row.feature_name,
            row.group_name.replace("_", " "),
            row.kpi_type_name,
            row.description,
            row.time_window_value,
            row.value_references,
            row.data_type,
            " ".join(observed_values),
        ]
    )


@lru_cache(maxsize=2)
def build_retrieval_index() -> RetrievalIndex:
    documents = []
    doc_freq: defaultdict[str, int] = defaultdict(int)
    total_length = 0
    vocabulary = column_value_vocabulary()

    for row in load_kpi_meta():
        text = document_text(row, vocabulary.get(row.feature_name, ()))
        terms = Counter(expand_tokens(tokens(text)))
        length = sum(terms.values()) or 1
        total_length += length
        documents.append(
            RetrievalDocument(
                row=row,
                text=text,
                term_counts=terms,
                semantic_vector=char_ngrams(text),
                length=length,
            )
        )
        for term in terms:
            doc_freq[term] += 1

    avgdl = total_length / max(len(documents), 1)
    return RetrievalIndex(tuple(documents), dict(doc_freq), avgdl)


@lru_cache(maxsize=8)
def client_column_usage(client: str) -> dict[str, int]:
    """How many of the client's production VPs use each column.

    `client_column_prior` answers only "ever used?", and that cannot separate two
    columns documented identically. `L_AGG_MSISDN` and `L_AGG_CNT` share a
    description word for word, so a binary prior gave both the same evidence and
    the lexically closer name won — even though production uses one of them in
    50 rules and the other in 4.
    """
    try:
        rows = load_vp_descriptions(client)
    except (OSError, ValueError):
        return {}

    known = {row.feature_name for row in load_kpi_meta()}
    counts: Counter[str] = Counter()
    for row in rows:
        condition = row.get("PARENT_CONDITION", "")
        # Per rule, not per mention: "used by 50 VPs" is the useful signal, and
        # a column repeated inside one condition is still one rule.
        counts.update({token for token in COLUMN_RE.findall(condition) if token in known})
    return dict(counts)


AGGREGATED_ROLE_RE = re.compile(r"\b(?:SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)")
PAIR_OWNER_ROLE_RE = re.compile(
    r"((?:SUM|COUNT_ALL|AVG|MAX|MIN)\([^)]*\)(?:__groupby_[A-Za-z0-9_,]+)?|[A-Za-z][A-Za-z0-9_]*)"
    r"\s*\$\{operator\}\s*\$\{value\}"
)
GROUPBY_ROLE_RE = re.compile(r"__groupby_([A-Za-z0-9_,]+)")


@lru_cache(maxsize=8)
def client_role_usage(client: str) -> dict[str, dict[str, int]]:
    """How often each column appears in each structural role, per client.

    A bare usage count cannot separate two columns that both appear often but do
    different jobs. `L_ACTION_KEY` carries `${operator} ${value}` 34 times and is
    counted 21; `L_AGG_MSISDN` is counted 52 times and carries the pair almost
    never. Filling a seed's `{key_col}` and `{count_col}` from an undifferentiated
    pool therefore came down to list order, and one run swapped them.

    Roles: `aggregated` (inside an aggregate call), `pair_owner` (carries the
    runtime pair), `groupby` (named by a `__groupby_` suffix).
    """
    try:
        rows = load_vp_descriptions(client)
    except (OSError, ValueError):
        return {}

    known = {row.feature_name for row in load_kpi_meta()}
    usage: dict[str, Counter[str]] = {
        "aggregated": Counter(),
        "pair_owner": Counter(),
        "groupby": Counter(),
    }
    for row in rows:
        condition = row.get("PARENT_CONDITION", "")
        usage["aggregated"].update(
            {column for column in AGGREGATED_ROLE_RE.findall(condition) if column in known}
        )
        owner = PAIR_OWNER_ROLE_RE.search(condition)
        if owner and "(" not in owner.group(1) and owner.group(1) in known:
            usage["pair_owner"][owner.group(1)] += 1
        for match in GROUPBY_ROLE_RE.findall(condition):
            usage["groupby"].update(
                {part.strip() for part in match.split(",") if part.strip() in known}
            )
    return {role: dict(counter) for role, counter in usage.items()}


@lru_cache(maxsize=8)
def client_selector_for_counted(client: str) -> dict[str, tuple[tuple[str, int], ...]]:
    """For each counted column, which column production pairs it with.

    When a seed needs a `{key_col}` and the agent supplied no selector, the slot
    is left empty rather than filled with the metric. That is honest but not
    useful on its own: the client's own rules already say which selector belongs
    with a given count. Every production rule counting `L_AGG_MSISDN` puts the
    runtime pair on `L_ACTION_KEY` (31) or `LC_SEGMENT_NAME` (21).
    """
    try:
        rows = load_vp_descriptions(client)
    except (OSError, ValueError):
        return {}

    known = {row.feature_name for row in load_kpi_meta()}
    pairs: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        condition = row.get("PARENT_CONDITION", "")
        counted = {c for c in AGGREGATED_ROLE_RE.findall(condition) if c in known}
        if not counted:
            continue
        owner = PAIR_OWNER_ROLE_RE.search(condition)
        if not owner or "(" in owner.group(1) or owner.group(1) not in known:
            continue
        for column in counted:
            if column != owner.group(1):
                pairs[column][owner.group(1)] += 1
    return {
        column: tuple(counter.most_common(3)) for column, counter in pairs.items()
    }


@lru_cache(maxsize=8)
def client_column_prior(client: str) -> set[str]:
    try:
        rows = load_vp_descriptions(client)
    except FileNotFoundError:
        return set()

    known = {row.feature_name for row in load_kpi_meta()}
    found: set[str] = set()
    for row in rows:
        condition = row.get("PARENT_CONDITION", "")
        for token in COLUMN_RE.findall(condition):
            if token in known:
                found.add(token)
    return found


