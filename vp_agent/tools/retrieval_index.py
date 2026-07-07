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


def document_text(row: KpiMeta) -> str:
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
        ]
    )


@lru_cache(maxsize=2)
def build_retrieval_index() -> RetrievalIndex:
    documents = []
    doc_freq: defaultdict[str, int] = defaultdict(int)
    total_length = 0

    for row in load_kpi_meta():
        text = document_text(row)
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


