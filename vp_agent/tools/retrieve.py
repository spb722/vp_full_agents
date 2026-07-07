from __future__ import annotations

from typing import Any

from vp_agent.schemas import Candidate
from vp_agent.text import phrase_text, tokens
from vp_agent.tools.retrieval_index import (
    build_retrieval_index,
    char_ngrams,
    client_column_prior,
    expand_tokens,
)


DOMAIN_HINTS = {
    "recharge": {"recharge", "topup", "top", "denomination", "voucher"},
    "usage": {"usage", "data", "voice", "sms", "mou", "revenue"},
    "profile": {"nationality", "handset", "device", "age", "language", "status"},
    "subscription": {"subscription", "pack", "bundle", "product", "purchase"},
    "lifecycle": {"campaign", "bonus", "promo", "promotion", "action"},
}

BM25_WEIGHT = 0.5
EMBEDDING_WEIGHT = 0.5
METADATA_TIEBREAKER_WEIGHT = 0.03
CLIENT_PRIOR_TIEBREAKER_WEIGHT = 0.02


def retrieve_columns(slots: dict[str, Any], client: str, exclude: list[str] | None = None, top_k: int = 12) -> list[Candidate]:
    exclude_set = {item.lower() for item in (exclude or [])}
    query_text = phrase_text(slots)
    query_terms = expand_tokens(tokens(query_text))
    query_term_set = set(query_terms)
    query_vector = char_ngrams(query_text)
    main_phrase = str(slots.get("kpi_phrase") or slots.get("metric") or "")
    main_terms = set(expand_tokens(tokens(main_phrase)))
    time_token = str(slots.get("time_token") or "").lower()
    operator = str(slots.get("operator") or "")
    value = str(slots.get("value") or "")
    numeric_threshold = operator in {">", ">=", "<", "<=", "between"} or value.replace(".", "", 1).isdigit()
    domain = str(slots.get("domain", "")).lower()
    domain_terms = DOMAIN_HINTS.get(domain, set())
    index = build_retrieval_index()
    client_prior_columns = client_column_prior(client)

    raw_scores: list[dict[str, Any]] = []
    for doc in index.documents:
        row = doc.row
        if row.feature_name.lower() in exclude_set or row.id.lower() in exclude_set:
            continue

        bm25_score = index.bm25(query_terms, doc)
        semantic_score = 0.0
        if query_vector:
            from vp_agent.tools.retrieval_index import cosine

            semantic_score = cosine(query_vector, doc.semantic_vector)
        doc_terms = set(doc.term_counts)
        feature_terms = set(tokens(row.feature_name.replace("_", " ")))
        domain_overlap = len(doc_terms & domain_terms)
        main_overlap = len(doc_terms & main_terms)
        main_feature_overlap = len(feature_terms & main_terms)
        all_main_terms_match = bool(main_terms) and main_terms.issubset(doc_terms)
        exact_feature_bonus = 4 if row.feature_name.lower() in query_text.lower() else 0
        time_bonus = 0.0
        if row.time_window_value and row.time_window_value.lower() in {time_token, *query_term_set}:
            time_bonus = 8.0
        elif row.time_window_value and row.time_window_value.lower() in query_text.lower():
            time_bonus = 5.0
        group_bonus = 2.0 if row.group_name == "360_PROFILE" and time_bonus else 0.0
        numeric_bonus = 0.0
        if numeric_threshold:
            if row.data_type.lower() == "numeric":
                numeric_bonus = 5.0
            elif row.data_type.lower() in {"categorical", "string"}:
                numeric_bonus = -4.0
        main_bonus = main_overlap * 2.0 + main_feature_overlap * 3.0
        if all_main_terms_match:
            main_bonus += 10.0
        metadata_boost = domain_overlap * 0.75 + main_bonus + exact_feature_bonus + time_bonus + group_bonus + numeric_bonus
        client_prior = 1.0 if row.feature_name in client_prior_columns else 0.0

        if bm25_score <= 0 and semantic_score <= 0 and metadata_boost <= 0:
            continue

        raw_scores.append(
            {
                "row": row,
                "bm25": bm25_score,
                "semantic": semantic_score,
                "metadata": metadata_boost,
                "client_prior": client_prior,
            }
        )

    max_bm25 = max((item["bm25"] for item in raw_scores), default=1.0) or 1.0
    max_semantic = max((item["semantic"] for item in raw_scores), default=1.0) or 1.0
    max_metadata = max((abs(item["metadata"]) for item in raw_scores), default=1.0) or 1.0

    for item in raw_scores:
        item["bm25_norm"] = item["bm25"] / max_bm25
        item["embedding_norm"] = item["semantic"] / max_semantic
        item["hybrid"] = BM25_WEIGHT * item["bm25_norm"] + EMBEDDING_WEIGHT * item["embedding_norm"]
        item["score"] = (
            item["hybrid"]
            + METADATA_TIEBREAKER_WEIGHT * (item["metadata"] / max_metadata)
            + CLIENT_PRIOR_TIEBREAKER_WEIGHT * item["client_prior"]
        )

    def build_candidate(item: dict[str, Any], score: float) -> Candidate:
        row = item["row"]
        reason_bits = []
        if item["bm25"]:
            reason_bits.append(f"bm25={item['bm25']:.3f}")
        if item["semantic"]:
            reason_bits.append(f"embedding={item['semantic']:.3f}")
        reason_bits.append(
            f"hybrid=0.5*bm25_norm({item['bm25_norm']:.3f})+0.5*embedding_norm({item['embedding_norm']:.3f})"
        )
        if item["metadata"]:
            reason_bits.append(f"metadata_boost={item['metadata']:.1f}")
        if item["client_prior"]:
            reason_bits.append("seen in client production VPs")
        if row.time_window_value and row.time_window_value.lower() in {time_token, *query_term_set}:
            reason_bits.append("time window match")

        return Candidate(
            id=row.id,
            feature_name=row.feature_name,
            group_name=row.group_name,
            description=row.description,
            data_type=row.data_type,
            time_window_value=row.time_window_value,
            score=score,
            reason=", ".join(reason_bits),
            bm25_score=item["bm25"],
            semantic_score=item["semantic"],
            bm25_norm=item["bm25_norm"],
            embedding_norm=item["embedding_norm"],
            hybrid_score=item["hybrid"],
            metadata_boost=item["metadata"],
            client_prior=item["client_prior"],
        )

    scored: list[Candidate] = []
    for item in raw_scores:
        scored.append(build_candidate(item, item["score"]))

    scored.sort(key=lambda c: (c.score, c.group_name == "360_PROFILE", c.bm25_score), reverse=True)

    doc_by_id = {doc.row.id: doc for doc in index.documents}

    def phrase_candidates(phrase: str, limit: int = 2) -> list[Candidate]:
        phrase_terms = expand_tokens(tokens(phrase))
        if not phrase_terms:
            return []
        phrase_vector = char_ngrams(phrase)
        phrase_raw = []
        from vp_agent.tools.retrieval_index import cosine

        for item in raw_scores:
            row = item["row"]
            doc = doc_by_id[row.id]
            feature_terms = set(tokens(row.feature_name.replace("_", " ")))
            phrase_term_set = set(phrase_terms)
            feature_overlap = len(feature_terms & phrase_term_set)
            doc_overlap = len(set(doc.term_counts) & phrase_term_set)
            phrase_raw.append(
                {
                    "item": item,
                    "bm25": index.bm25(phrase_terms, doc),
                    "embedding": cosine(phrase_vector, doc.semantic_vector),
                    "metadata": feature_overlap * 5 + doc_overlap,
                }
            )
        max_phrase_bm25 = max((item["bm25"] for item in phrase_raw), default=1.0) or 1.0
        max_phrase_embedding = max((item["embedding"] for item in phrase_raw), default=1.0) or 1.0
        max_phrase_metadata = max((item["metadata"] for item in phrase_raw), default=1.0) or 1.0
        ranked = []
        for phrase_item in phrase_raw:
            bm25_norm = phrase_item["bm25"] / max_phrase_bm25
            embedding_norm = phrase_item["embedding"] / max_phrase_embedding
            phrase_score = (
                BM25_WEIGHT * bm25_norm
                + EMBEDDING_WEIGHT * embedding_norm
                + METADATA_TIEBREAKER_WEIGHT * (phrase_item["metadata"] / max_phrase_metadata)
            )
            if phrase_score > 0:
                ranked.append((phrase_score, phrase_item["item"]))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [build_candidate(item, score) for score, item in ranked[:limit]]

    filter_phrases = []
    for filter_item in slots.get("filters") or []:
        if isinstance(filter_item, dict):
            filter_phrases.append(" ".join(str(filter_item.get(key, "")) for key in ("phrase", "value")))
        else:
            filter_phrases.append(str(filter_item))

    selected: list[Candidate] = []
    seen_ids: set[str] = set()

    # Keep strong global KPI matches, but reserve room for every extracted filter.
    reserved_slots = min(len(filter_phrases) * 2, max(0, top_k - 4))
    global_limit = max(0, top_k - reserved_slots)
    for candidate in scored[:global_limit]:
        selected.append(candidate)
        seen_ids.add(candidate.id)

    for phrase in filter_phrases:
        for candidate in phrase_candidates(phrase, limit=2):
            if candidate.id in seen_ids:
                continue
            selected.append(candidate)
            seen_ids.add(candidate.id)
            if len(selected) >= top_k:
                break
        if len(selected) >= top_k:
            break

    for candidate in scored:
        if len(selected) >= top_k:
            break
        if candidate.id not in seen_ids:
            selected.append(candidate)
            seen_ids.add(candidate.id)

    return selected[:top_k]
