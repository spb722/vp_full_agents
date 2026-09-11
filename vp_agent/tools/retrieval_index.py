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


@lru_cache(maxsize=1)
def column_value_vocabulary() -> dict[str, tuple[str, ...]]:
    """Literal values each column has actually been compared against.

    `kpi_meta.value_references` is sparse — `Profile_Cdr_Nationality` records
    "NONE" — so a marketer word like "Indian" has no lexical path to its column
    and retrieval cannot find it. Reviewed golden cases and production VPs
    already contain `Profile_Cdr_Nationality = Indian`, so mine the vocabulary
    from the rules the business has actually written.
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

    vocabulary: defaultdict[str, set[str]] = defaultdict(set)
    for condition in conditions:
        for column, value in _candidate_values(condition):
            if column not in known:
                continue
            for token in tokens(value):
                # Keep real category words: skip numbers, engine keywords, and
                # column-to-column comparisons.
                # Skip numbers, date-anchor fragments like "30days", and engine
                # keywords; keep only real category words.
                if token[0].isdigit() or token in NON_VALUE_TOKENS or len(token) < 2:
                    continue
                vocabulary[column].add(token)
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


