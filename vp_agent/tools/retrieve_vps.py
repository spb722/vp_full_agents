from __future__ import annotations

from typing import Any

from vp_agent.data import load_vp_descriptions
from vp_agent.text import tokens
from vp_agent.tools.retrieval_index import char_ngrams, cosine, expand_tokens


def retrieve_existing_vps(
    query: str,
    client: str,
    exclude: list[str] | None = None,
    top_k: int = 12,
) -> list[dict[str, Any]]:
    """Return ranked existing VP evidence without deciding semantic validity."""

    query_text = str(query or "").strip()
    query_terms = set(expand_tokens(tokens(query_text)))
    query_vector = char_ngrams(query_text)
    excluded = {str(value).strip().lower() for value in (exclude or [])}
    ranked: list[dict[str, Any]] = []

    for row in load_vp_descriptions(client):
        name = str(row.get("VIRTUAL_PROFILE_NAME") or "").strip()
        condition = str(row.get("PARENT_CONDITION") or "").strip()
        if not name or name.lower() in excluded:
            continue

        evidence_text = f"{name.replace('_', ' ')} {condition}"
        evidence_terms = set(expand_tokens(tokens(evidence_text)))
        name_terms = set(expand_tokens(tokens(name.replace("_", " "))))
        overlap = sorted(query_terms & evidence_terms)
        coverage = len(overlap) / max(len(query_terms), 1)
        name_precision = len(query_terms & name_terms) / max(len(name_terms), 1)
        semantic = cosine(query_vector, char_ngrams(evidence_text)) if query_vector else 0.0
        score = 0.45 * coverage + 0.25 * name_precision + 0.3 * semantic
        if score <= 0:
            continue

        ranked.append(
            {
                "name": name,
                "parent_condition": condition,
                "score": round(score, 6),
                "reason": (
                    f"token_coverage={coverage:.3f}, name_precision={name_precision:.3f}, "
                    f"semantic={semantic:.3f}, overlap={','.join(overlap)}"
                ),
                "runtime_placeholders": {
                    "operator": "${operator}" in condition,
                    "value": "${value}" in condition,
                },
                "source": f"vpdesc-all-{client.lower().strip()}.csv",
            }
        )

    ranked.sort(key=lambda item: (item["score"], item["name"]), reverse=True)
    return ranked[: max(1, top_k)]
