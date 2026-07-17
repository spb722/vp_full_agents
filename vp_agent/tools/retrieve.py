from __future__ import annotations

from typing import Any
import re

from vp_agent.schemas import Candidate
from vp_agent.domain_config import DOMAIN_GROUP_PREFERENCES, date_column_for_group
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


def retrieve_columns(slots: dict[str, Any], client: str, exclude: list[str] | None = None, top_k: int = 5) -> list[Candidate]:
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


SNAPSHOT_TOKEN_RE = re.compile(
    r"(^|_)(?:M(?:TD|[1-9]|1[0-2])|LMTD|W[1-9]|FW\d+|\d+D(?:_\d+D)?)(_|$)",
    re.I,
)


def _short_text(value: object, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _time_token(slots: dict[str, Any]) -> str:
    token = str(slots.get("time_token") or "none").strip().upper()
    return token if token not in {"", "UNKNOWN", "NULL"} else "NONE"


def _canonical_time_token(value: object) -> str:
    token = str(value or "").strip().upper().replace("WK", "W")
    full_week = re.fullmatch(r"FW(\d+)", token)
    if full_week:
        return f"W{full_week.group(1)}"
    match = re.fullmatch(r"(\d+)W", token)
    if match:
        return f"W{match.group(1)}"
    match = re.fullmatch(r"(\d+)M", token)
    if match:
        return f"M{match.group(1)}"
    return token


def _is_snapshot(candidate: Candidate) -> bool:
    if candidate.group_name != "360_PROFILE":
        return False
    return bool(candidate.time_window_value.strip() or SNAPSHOT_TOKEN_RE.search(candidate.feature_name))


def _snapshot_matches(candidate: Candidate, requested_time: str) -> bool:
    if requested_time == "NONE":
        return False
    requested_time = _canonical_time_token(requested_time)
    metadata_time = _canonical_time_token(candidate.time_window_value)
    if metadata_time:
        return metadata_time == requested_time
    feature = candidate.feature_name.upper().replace("WK", "W")
    aliases = {requested_time}
    week_match = re.fullmatch(r"W(\d+)", requested_time)
    if week_match:
        aliases.add(f"{week_match.group(1)}W")
    month_match = re.fullmatch(r"M(\d+)", requested_time)
    if month_match:
        aliases.add(f"{month_match.group(1)}M")
    return any(re.search(rf"(^|_){re.escape(alias)}(_|$)", feature) for alias in aliases)


def _time_support(candidate: Candidate, slots: dict[str, Any]) -> tuple[str, list[str]]:
    requested_time = _time_token(slots)
    snapshot = _is_snapshot(candidate)
    if requested_time == "NONE":
        if snapshot:
            return "period snapshot not requested", ["period_snapshot_without_requested_period"]
        return "no time window required", []
    if snapshot:
        if _snapshot_matches(candidate, requested_time):
            return f"exact {requested_time} snapshot", []
        return "snapshot period mismatch", ["snapshot_period_mismatch"]
    date_column = date_column_for_group(candidate.group_name, slots)
    if date_column:
        return "custom rolling window", []
    return "no configured event date", ["missing_group_date_configuration"]


def _role_gate_failures(candidate: Candidate, slots: dict[str, Any], role: str) -> list[str]:
    failures: list[str] = []
    data_type = candidate.data_type.lower()
    aggregate = str(slots.get("aggregate") or "").upper()
    formula = slots.get("formula")
    if isinstance(formula, dict) and (formula.get("type") or formula.get("formula_type")):
        aggregate = "FORMULA"
    phrase = str(slots.get("kpi_phrase") or slots.get("metric") or "").lower()

    if role == "metric":
        if data_type == "date" and not any(term in phrase for term in ("date", "day", "time")):
            failures.append("metric_requires_non_date_column")
        if aggregate in {"SUM", "AVG", "MAX", "FORMULA"} and data_type in {"date", "string", "categorical"}:
            failures.append("aggregate_requires_numeric_metric")
        feature = candidate.feature_name.upper()
        if _is_snapshot(candidate):
            if aggregate == "SUM" and re.search(r"(^|_)(?:MAX|AVG)(_|$)", feature):
                failures.append("snapshot_aggregate_mismatch")
            elif aggregate == "AVG" and "AVG" not in feature:
                # Raw event candidates remain eligible for an average formula;
                # only a precomputed snapshot must encode AVG explicitly.
                failures.append("snapshot_average_not_encoded")
        _, time_failures = _time_support(candidate, slots)
        failures.extend(time_failures)
    elif role.startswith("filter:"):
        operator = str(slots.get("operator") or "").upper()
        value = slots.get("value")
        scalar = str(value or "").replace(".", "", 1)
        if operator in {">", ">=", "<", "<="} and scalar.isdigit() and data_type in {"date", "string", "categorical"}:
            failures.append("numeric_filter_requires_numeric_column")
    return list(dict.fromkeys(failures))


def _group_preference(candidate: Candidate, slots: dict[str, Any], metric_group: str | None = None) -> float:
    domain = str(slots.get("domain") or "").lower()
    preferred = DOMAIN_GROUP_PREFERENCES.get(domain, ())
    bonus = 0.0
    if candidate.group_name in preferred:
        bonus += max(0.0, 0.035 - preferred.index(candidate.group_name) * 0.01)
    if metric_group and candidate.group_name == metric_group:
        bonus += 0.025
    return bonus


def _qualifier_adjustment(candidate: Candidate, slots: dict[str, Any]) -> float:
    """Prefer explicit business families without making them final truth."""
    query_terms = set(tokens(slots.get("kpi_phrase") or slots.get("metric") or ""))
    feature_terms = set(tokens(candidate.feature_name.replace("_", " ")))
    candidate_terms = set(
        tokens(
            " ".join(
                (
                    candidate.feature_name.replace("_", " "),
                    candidate.description,
                    candidate.group_name.replace("_", " "),
                )
            )
        )
    )
    adjustment = 0.0

    wants_revenue = bool(query_terms & {"revenue", "spend", "spent", "amount"})
    wants_usage = bool(query_terms & {"usage", "used", "volume", "mou"}) and not wants_revenue
    has_revenue = bool(candidate_terms & {"revenue", "rev", "amount"})
    has_usage = bool(candidate_terms & {"usage", "volume", "mou"})
    if wants_revenue:
        adjustment += 0.09 if has_revenue else -0.10
    elif wants_usage:
        adjustment += 0.09 if has_usage else -0.12 if has_revenue else 0.0

    service_terms = {
        "data": {"data"},
        "voice": {"voice", "call", "calls"},
        "sms": {"sms", "message", "messages"},
        "recharge": {"recharge", "topup", "denomination"},
        "subscription": {"subscription", "subscriptions", "product", "purchase", "purchases"},
    }
    requested_services = [name for name, terms_set in service_terms.items() if query_terms & terms_set]
    for service in requested_services:
        has_service = bool(candidate_terms & service_terms[service])
        adjustment += 0.045 if has_service else -0.045
    if (
        wants_revenue
        and not requested_services
        and not query_terms & {"finance", "financial"}
        and any(feature_terms & terms_set for terms_set in service_terms.values())
    ):
        # A generic "total revenue" request must not be crowded out by voice,
        # SMS, data, recharge, or subscription specializations merely because
        # they share the word revenue and the requested period.
        adjustment -= 0.075

    qualifier_terms = {
        "local": {"local"},
        "onnet": {"onnet"},
        "offnet": {"offnet"},
        "roaming": {"roaming"},
        "outgoing": {"outgoing", "og"},
        "incoming": {"incoming", "ic"},
        "prepaid": {"prepaid", "prepay"},
        "postpaid": {"postpaid", "postpay"},
        "payg": {"payg", "pay", "go"},
        "bundle": {"bundle", "bundled"},
        "free": {"free"},
        "finance": {"finance", "financial"},
        "international": {"international", "idd"},
    }
    for qualifier, aliases in qualifier_terms.items():
        if query_terms & aliases:
            adjustment += 0.055 if candidate_terms & aliases else -0.045
        elif feature_terms & aliases:
            # Specializations not requested by the marketer stay auditable but
            # rank below a matching generic KPI family. Use only the feature
            # name here: generic KPI descriptions often enumerate all scopes
            # they include and must not be mistaken for a specialization.
            adjustment -= 0.035
    return adjustment


def _candidate_evidence(candidate: Candidate, slots: dict[str, Any], time_support: str, group_bonus: float) -> str:
    evidence: list[str] = []
    if candidate.bm25_norm >= 0.7 and candidate.embedding_norm >= 0.6:
        evidence.append("Strong lexical and semantic match")
    elif candidate.bm25_norm >= candidate.embedding_norm:
        evidence.append("Strong metadata/term match")
    else:
        evidence.append("Strong semantic match")
    if "exact" in time_support:
        evidence.append(f"supports the requested {_time_token(slots)} period directly")
    elif time_support == "custom rolling window":
        date_column = date_column_for_group(candidate.group_name, slots)
        evidence.append(f"supports the requested window through configured event date {date_column}")
    if group_bonus:
        evidence.append("fits the preferred metric/filter group plan")
    return _short_text("; ".join(evidence) + ".", 180)


def compact_candidate(
    candidate: Candidate,
    slots: dict[str, Any],
    *,
    group_bonus: float = 0.0,
    qualifier_adjustment: float = 0.0,
) -> dict[str, Any]:
    time_support, _ = _time_support(candidate, slots)
    return {
        "candidate_id": candidate.id,
        "feature_name": candidate.feature_name,
        "group_name": candidate.group_name,
        "data_type": candidate.data_type,
        "description": _short_text(candidate.description),
        "time_window_support": time_support,
        "score": round(candidate.score + group_bonus + qualifier_adjustment, 3),
        "evidence": _candidate_evidence(candidate, slots, time_support, group_bonus),
    }


def _rank_role(
    slots: dict[str, Any],
    client: str,
    role: str,
    exclude: list[str] | None,
    metric_group: str | None = None,
) -> list[dict[str, Any]]:
    # Request a number larger than the metadata corpus so the audit retains the
    # complete positive-score ranking. Only the compact page enters model context.
    candidates = retrieve_columns(slots, client=client, exclude=exclude, top_k=10000)
    ranked: list[dict[str, Any]] = []
    for raw_rank, candidate in enumerate(candidates, start=1):
        failures = _role_gate_failures(candidate, slots, role)
        group_bonus = _group_preference(candidate, slots, metric_group)
        qualifier_adjustment = _qualifier_adjustment(candidate, slots)
        ranked.append(
            {
                "candidate": candidate,
                "raw_rank": raw_rank,
                "eligible": not failures,
                "gate_failures": failures,
                "group_bonus": group_bonus,
                "qualifier_adjustment": qualifier_adjustment,
                "reranked_score": candidate.score + group_bonus + qualifier_adjustment,
            }
        )
    ranked.sort(
        key=lambda item: (
            item["eligible"],
            item["reranked_score"],
            -item["raw_rank"],
        ),
        reverse=True,
    )
    for reranked_rank, item in enumerate(ranked, start=1):
        item["reranked_rank"] = reranked_rank
    return ranked


def _metric_role_slots(slots: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in slots.items() if key not in {"filters", "negations"}}


def _filter_role_slots(slots: dict[str, Any], filter_item: dict[str, Any]) -> dict[str, Any]:
    phrase = str(filter_item.get("phrase") or filter_item.get("name") or filter_item.get("field") or "")
    value = filter_item.get("value")
    return {
        "raw_request": slots.get("raw_request", ""),
        "domain": "profile",
        "kpi_phrase": " ".join(part for part in (phrase, str(value or "")) if part),
        "time_token": "none",
        "operator": filter_item.get("operator") or "=",
        "value": value,
        "filters": [filter_item],
    }


def _role_id(index: int, filter_item: dict[str, Any]) -> str:
    phrase = str(filter_item.get("phrase") or filter_item.get("name") or filter_item.get("field") or "filter")
    slug = "_".join(tokens(phrase)[:4]) or "filter"
    return f"filter_{index + 1}_{slug}"


def build_retrieval_audit(
    slots: dict[str, Any],
    client: str,
    exclude: list[str] | None = None,
) -> dict[str, Any]:
    metric_slots = _metric_role_slots(slots)
    metric_ranking = _rank_role(metric_slots, client, "metric", exclude)
    eligible_metrics = [item for item in metric_ranking if item["eligible"]]
    metric_group = eligible_metrics[0]["candidate"].group_name if eligible_metrics else None

    roles: dict[str, dict[str, Any]] = {
        "metric": {
            "role": "metric",
            "phrase": str(slots.get("kpi_phrase") or slots.get("metric") or ""),
            "slots": metric_slots,
            "ranking": metric_ranking,
        }
    }
    for index, raw_filter in enumerate(slots.get("filters") or []):
        filter_item = raw_filter if isinstance(raw_filter, dict) else {"phrase": str(raw_filter), "value": str(raw_filter)}
        role_id = _role_id(index, filter_item)
        filter_slots = _filter_role_slots(slots, filter_item)
        roles[role_id] = {
            "role": role_id,
            "phrase": str(filter_item.get("phrase") or ""),
            "predicate": filter_item,
            "slots": filter_slots,
            "ranking": _rank_role(filter_slots, client, role_id, exclude, metric_group),
        }
    return {
        "client": client,
        "slots": slots,
        "requested_time": _time_token(slots),
        "metric_group": metric_group,
        "roles": roles,
    }


def compact_retrieval_page(
    audit: dict[str, Any],
    *,
    audit_id: str,
    role_ids: list[str] | None = None,
    page: int = 1,
    limit_per_role: int = 5,
) -> dict[str, Any]:
    page = max(1, min(page, 3))
    limit_per_role = max(1, min(limit_per_role, 5))
    start = (page - 1) * 5
    stop = start + limit_per_role
    selected_roles = role_ids or list(audit["roles"])

    metric_candidates: list[dict[str, Any]] = []
    filter_candidates: list[dict[str, Any]] = []
    expandable_roles: list[str] = []
    for role_id in selected_roles:
        role = audit["roles"].get(role_id)
        if not role:
            continue
        eligible = [item for item in role["ranking"] if item["eligible"]]
        page_items = eligible[start:stop]
        compact = [
            compact_candidate(
                item["candidate"],
                role["slots"],
                group_bonus=item["group_bonus"],
                qualifier_adjustment=item["qualifier_adjustment"],
            )
            for item in page_items
        ]
        if len(eligible) > stop and page < 3:
            expandable_roles.append(role_id)
        if role_id == "metric":
            metric_candidates = compact
        else:
            filter_candidates.append(
                {
                    "role_id": role_id,
                    "phrase": role["phrase"],
                    "candidates": compact,
                }
            )

    requested_time = audit["requested_time"]
    primary_time_support = (
        metric_candidates[0]["time_window_support"]
        if metric_candidates
        else "unresolved"
    )
    exact_snapshot_found = primary_time_support.startswith("exact ")
    return {
        "audit_id": audit_id,
        "page": page,
        "metric_candidates": metric_candidates,
        "filter_candidates": filter_candidates,
        "time_assessment": {
            "requested_time": requested_time,
            "exact_snapshot_found": exact_snapshot_found,
            "requires_event_date": requested_time != "NONE" and not exact_snapshot_found,
        },
        "expandable_roles": expandable_roles,
    }


def serialize_retrieval_audit(audit: dict[str, Any]) -> dict[str, Any]:
    serialized_roles: dict[str, Any] = {}
    for role_id, role in audit["roles"].items():
        ranking = []
        for item in role["ranking"]:
            candidate = item["candidate"]
            ranking.append(
                {
                    **candidate.__dict__,
                    "raw_rank": item["raw_rank"],
                    "reranked_rank": item["reranked_rank"],
                    "eligible": item["eligible"],
                    "gate_failures": item["gate_failures"],
                    "group_bonus": item["group_bonus"],
                    "qualifier_adjustment": item["qualifier_adjustment"],
                    "reranked_score": item["reranked_score"],
                    "time_window_support": _time_support(candidate, role["slots"])[0],
                }
            )
        serialized_roles[role_id] = {
            "role": role["role"],
            "phrase": role["phrase"],
            "predicate": role.get("predicate"),
            "ranking": ranking,
        }
    return {
        "client": audit["client"],
        "slots": audit["slots"],
        "requested_time": audit["requested_time"],
        "metric_group": audit["metric_group"],
        "roles": serialized_roles,
    }
