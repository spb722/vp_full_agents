from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from vp_agent.data import load_kpi_meta, load_vp_descriptions
from vp_agent.text import tokens


CONDITION_SIGNATURE_RE = re.compile(r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(|Current(?:Time|Month|Week)|\$\{operator\}|\$\{value\}")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
PLACEHOLDER_OWNER_RE = re.compile(
    r"((?:SUM|COUNT_ALL|AVG|MAX|MIN)\([^)]*\)(?:__groupby_[A-Za-z0-9_,]+)?|[A-Za-z][A-Za-z0-9_]*)"
    r"\s*\$\{operator\}\s*\$\{value\}"
)
AGGREGATED_COLUMN_RE = re.compile(r"(?:SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)")
AGGREGATED_PAIRS_RE = re.compile(r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)")
FIXED_AGGREGATE_RE = re.compile(
    r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)\s*(<=|>=|<>|!=|<|>|=)\s*(-?\d+(?:\.\d+)?)"
)
IDENTIFIER_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*(?:_\$\{X\})?[A-Za-z0-9_]*\b")
FUNCTIONS = {"SUM", "COUNT_ALL", "AVG", "MAX", "MIN", "IN", "V", "f"}
KEYWORDS = {
    "AND",
    "OR",
    "NULL",
    "CurrentTime",
    "CurrentMonth",
    "CurrentWeek",
    "DAYS",
    "MONTHS",
    "WEEKS",
}


def has_condition_signature(text: str) -> bool:
    return bool(CONDITION_SIGNATURE_RE.search(text))


def _known_columns() -> set[str]:
    return {row.feature_name for row in load_kpi_meta()}


def referenced_columns(rule: str) -> set[str]:
    known = _known_columns()
    refs = set()
    for token in IDENTIFIER_RE.findall(rule):
        if token in FUNCTIONS or token in KEYWORDS:
            continue
        if token in known:
            refs.add(token)
    return refs


NULL_GUARD_RE = re.compile(
    r"((?:\b[A-Z][A-Za-z0-9_]*\s*\(\s*)?[A-Za-z][A-Za-z0-9_]*\)?)\s*(?:<>|!=)\s*NULL", re.I
)


def _null_guards(rule: str) -> set[str]:
    """Presence guards such as `Max(L_PROMO_SENT_DATE) <> NULL`.

    A guard is a whole clause, so losing one changes the audience silently. It
    was invisible to the production comparison, which only knew how to look at
    the placeholder owner and the aggregate threshold.
    """
    return {" ".join(match.split()) for match in NULL_GUARD_RE.findall(rule)}


def _rule_shape(rule: str) -> dict[str, Any]:
    owner_match = PLACEHOLDER_OWNER_RE.search(rule)
    owner = owner_match.group(1) if owner_match else None
    return {
        "placeholder_owner": owner,
        "placeholder_owner_kind": ("aggregate" if owner and "(" in owner else "column" if owner else None),
        "fixed_aggregate_comparisons": [
            f"{function}({column}) {operator} {value}"
            for function, column, operator, value in FIXED_AGGREGATE_RE.findall(rule)
        ],
        "null_guards": _null_guards(rule),
    }


def _focus_columns(rule: str) -> set[str]:
    """The columns that decide whether two rules are about the same thing.

    Prefer the aggregated column: two rules that aggregate different columns are
    different business questions even when they share a filter column, and
    comparing those produces noise. Fall back to the profiled column only when
    the rule has no aggregate at all, such as a 360 snapshot comparison.
    """
    known = _known_columns()
    focus = set(AGGREGATED_COLUMN_RE.findall(rule))
    focus.update(column for _, column, _, _ in FIXED_AGGREGATE_RE.findall(rule))
    if not focus:
        owner = _rule_shape(rule)["placeholder_owner"]
        if owner:
            focus.update(re.findall(r"[A-Za-z][A-Za-z0-9_]*", owner))
    return {column for column in focus if column in known}


def unrendered_stated_numbers(rule: str, request: str) -> list[str]:
    """Numbers the marketer stated that never reached the rendered rule.

    Dropping a stated number is sometimes correct — the main KPI threshold
    becomes `${operator} ${value}` by design — so this is reported as a warning
    for the agent to adjudicate, never as an error.
    """
    missing: list[str] = []
    for number in NUMBER_RE.findall(request or ""):
        if re.search(rf"(?<!\d){re.escape(number)}(?!\d)", rule):
            continue
        if number not in missing:
            missing.append(number)
    return missing


@dataclass(frozen=True)
class IntentCue:
    """A phrase in the request that should leave a visible mark on the rule."""

    name: str
    in_request: re.Pattern[str]
    in_rule: re.Pattern[str]
    message: str


INTENT_CUES: tuple[IntentCue, ...] = (
    IntentCue(
        "negation",
        re.compile(r"\b(not|never|without|excluding|exclude|haven'?t|hasn'?t|didn'?t|did not|no longer|non-)\b", re.I),
        # `<> NULL` is a presence guard, not a negation, so it does not satisfy this cue.
        re.compile(r"=\s*0\b|!=|<>\s*(?!NULL)|NOT\s+IN\s+LIST|NOT\s+LIKE", re.I),
        "the request looks negative but the rule has no negative condition; a positive rule selects the opposite audience",
    ),
    IntentCue(
        "per_entity",
        # "per month" is a period and "per customer" is the default grain, so
        # neither counts as a grouping request.
        re.compile(
            r"\bper\s+(?!day|days|week|weeks|month|months|year|years|hour|hours|subscriber|subscribers|customer|customers|user|users)\w+"
            r"|\bfor each\b|\bbroken down by\b|\bgrouped by\b",
            re.I,
        ),
        re.compile(r"__groupby_"),
        "the request asks for a per-entity figure but the rule has no __groupby_ suffix; it currently totals per subscriber",
    ),
    IntentCue(
        "average",
        re.compile(r"\b(average|avg|mean)\b", re.I),
        re.compile(r"\bAVG\s*\(|/\s*\d+|f\s*\{[^}]*/", re.I),
        "the request asks for an average but the rule has no AVG aggregate and no divisor formula",
    ),
    IntentCue(
        "alternation",
        re.compile(r"\bor\b(?!\s+(?:more|less|equal|above|below|higher|lower|greater|fewer))", re.I),
        re.compile(r"\bIN\s+LIST\b|\bOR\b", re.I),
        "the request offers alternatives but the rule has no IN LIST or OR; ANDing alternatives for one column always matches nobody",
    ),
)

# scope name -> (how the marketer says it, how a column spells it)
SCOPE_CUES: dict[str, tuple[re.Pattern[str], tuple[str, ...]]] = {
    "local": (re.compile(r"\blocal\b", re.I), ("local",)),
    "offnet": (re.compile(r"\boff[- ]?net\b", re.I), ("offnet",)),
    "onnet": (re.compile(r"\bon[- ]?net\b", re.I), ("onnet",)),
    "idd": (re.compile(r"\b(idd|international)\b", re.I), ("idd", "international")),
    "roaming": (re.compile(r"\broam(?:ing)?\b", re.I), ("roam", "roaming")),
    "prepaid": (re.compile(r"\bpre[- ]?paid\b", re.I), ("prepaid", "prepay")),
    "postpaid": (re.compile(r"\bpost[- ]?paid\b", re.I), ("postpaid", "postpay")),
    "bundle": (re.compile(r"\bbundle[ds]?\b", re.I), ("bundle",)),
    "payg": (re.compile(r"\b(payg|pay[- ]as[- ]you[- ]go)\b", re.I), ("payg",)),
    "free": (re.compile(r"\bfree\b", re.I), ("free",)),
    "finance": (re.compile(r"\bfinanc(?:e|ial)\b", re.I), ("finance", "financial")),
}


def _rule_words(rule: str) -> str:
    """Rule text with underscores as separators, so column parts match as words."""
    return re.sub(r"[_\s]+", " ", rule).lower()


GENERIC_VALUE_TOKENS = frozenset({"yes", "no", "true", "false", "all", "none", "other", "null"})
# Column-name parts too common to prove a concept is present.
COLUMN_NAME_STOPWORDS = frozenset(
    {
        "type", "id", "name", "date", "dt", "count", "cnt", "common", "cust", "profile",
        "cdr", "group", "key", "flag", "code", "num", "total", "seg", "fct", "event",
        "value", "col", "amount", "rev",
    }
)


@lru_cache(maxsize=1)
def _value_column_index() -> dict[str, tuple[str, ...]]:
    """Invert the mined vocabulary into value -> columns that carry it."""
    from vp_agent.tools.retrieval_index import column_value_vocabulary

    index: dict[str, set[str]] = {}
    for column, values in column_value_vocabulary().items():
        for value in values:
            index.setdefault(value, set()).add(column)
    return {value: tuple(sorted(columns)) for value, columns in index.items()}


def dropped_category_values(rule: str, request: str) -> list[dict[str, Any]]:
    """Categorical values the request names that the rule does not express.

    Driven by the values production rules have actually used, so it covers
    promotion, bonus, DPI protocol, bundle type and channel without a
    hand-written list — the hand-written lists are what keep going stale. It
    runs against the finished rule, so unlike the retrieval-time check it sees
    the column the agent really chose rather than the one that ranked first.
    """
    index = _value_column_index()
    rule_words = _rule_words(rule)
    missing: list[dict[str, Any]] = []

    for token in dict.fromkeys(tokens(request)):
        if len(token) < 4 or token in GENERIC_VALUE_TOKENS:
            continue
        columns = index.get(token) or index.get(token.rstrip("s")) or ()
        if not columns:
            continue
        # The value itself appearing in the rule settles it.
        if re.search(rf"\b{re.escape(token[:5])}", rule_words):
            continue
        # Otherwise accept a distinctive part of any column known to carry it,
        # so a different column for the same concept still counts as covered.
        column_terms = {
            part
            for column in columns
            for part in tokens(column.replace("_", " "))
            if part not in COLUMN_NAME_STOPWORDS and len(part) > 2
        }
        if any(re.search(rf"\b{re.escape(part)}", rule_words) for part in column_terms):
            continue
        missing.append({"value": token, "seen_on": list(columns)[:3]})
    return missing


def intent_cue_warnings(rule: str, request: str) -> list[dict[str, Any]]:
    """Flag request phrasing that left no trace on the rendered rule.

    Purely textual and deliberately independent of the skills: it does not know
    what the agent was told, so it cannot inherit the agent's blind spots. Every
    result is advisory — some cues are legitimately satisfied in ways this
    cannot see, and the agent adjudicates.
    """
    if not request:
        return []

    warnings: list[dict[str, Any]] = []
    for cue in INTENT_CUES:
        match = cue.in_request.search(request)
        if match and not cue.in_rule.search(rule):
            warnings.append(
                {
                    "class": "intent",
                    "cue": cue.name,
                    "message": cue.message,
                    "request_phrase": match.group(0),
                }
            )

    rule_words = _rule_words(rule)
    missing_scopes = [
        scope
        for scope, (in_request, aliases) in SCOPE_CUES.items()
        if in_request.search(request)
        and not any(re.search(rf"\b{alias}", rule_words) for alias in aliases)
    ]
    dropped_values = dropped_category_values(rule, request)
    if dropped_values:
        warnings.append(
            {
                "class": "intent",
                "cue": "category_value",
                "message": (
                    "the request names a value that existing rules record on a specific column, but "
                    "neither the value nor that column appears in this rule; it is probably a filter "
                    "that was folded into the KPI phrase and lost"
                ),
                "values": dropped_values,
            }
        )

    if missing_scopes:
        warnings.append(
            {
                "class": "intent",
                "cue": "scope",
                "message": (
                    "the request names a service scope that does not appear in any chosen column; "
                    "confirm the column really covers that scope, since a column can encode it without naming it"
                ),
                "scopes": missing_scopes,
            }
        )
    return warnings


# "a particular promotion", "based on segment name", "for a given plan" name a
# knob the marketer sets later; "any promotion", "at all" say the opposite.
SELECTOR_CUE_RE = re.compile(
    r"\b(?:a\s+)?(?:particular|specific|given|certain|chosen|named)\b|\bbased\s+on\b|\bby\s+(?:segment|action\s+key|plan|product)\b",
    re.I,
)
ANY_SCOPE_RE = re.compile(r"\bany\b|\bat\s+all\b|\bwhatsoever\b", re.I)
RUNTIME_VARIABLE_RE = re.compile(r"(?<!\$)\$(?!\{)[A-Za-z_][A-Za-z0-9_]*")
CONTROL_GROUP_PAIR_RE = re.compile(
    r"\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\$\{operator\}\s*\$\{value\}\s+OR\s+"
    r"\1\s*\$\{operator\}\s*\$\{value\}_CG\s*\)",
    re.I,
)


def _collapse_control_group_pair(rule: str) -> str:
    """Treat `(COL ${operator} ${value} OR COL ${operator} ${value}_CG)` as one knob.

    The marketer supplies a single action key; the rule also matches that key's
    control-group twin, which the client writes by suffixing `_CG`. Textually
    that is two pairs, but it is one runtime value used twice, and four
    production VPs are written this way — `L_AK_PROMO_COUNT`, `L_AK_BONUS_COUNT`,
    `L_AK_PROMO_COUNT_3D`, `L_PROMO_DT`. Counting the placeholders naively
    rejected the correct answer to any control-group request.
    """
    return CONTROL_GROUP_PAIR_RE.sub(lambda m: f"{m.group(1)} ${{operator}} ${{value}}", rule)


COUNT_LIMIT_RE = re.compile(
    r"\b(?:at\s+most|at\s+least|no\s+more\s+than|not\s+more\s+than|fewer\s+than|less\s+than|"
    r"more\s+than|exactly|only)\s+\d+|\b\d+\s*(?:or\s+(?:more|fewer|less)|times?\b)",
    re.I,
)
TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def seed_clause_regressions(rule: str, seed_template: str) -> list[dict[str, Any]]:
    """Structural clauses the chosen seed carries that the rendered rule lost.

    A seed is a reviewed skeleton, so dropping one of its clauses is a silent
    change of meaning. One run selected `S42_promo_groupby_max` — whose template
    ends `AND Max({date_col}) <> NULL` — rendered the first three clauses and
    dropped the fourth, which was exactly the "confirmed by the sent date" the
    request asked for. Column names are compared through the template's
    `{placeholders}`, so only shape is checked, never the substitutions.
    """
    if not rule or not seed_template:
        return []

    missing: list[dict[str, Any]] = []
    if _null_guards(TEMPLATE_PLACEHOLDER_RE.sub("X", seed_template)) and not _null_guards(rule):
        missing.append(
            {
                "clause": "null_guard",
                "detail": "the seed template carries a `<> NULL` presence guard; the rule has none",
            }
        )
    if "__groupby_" in seed_template and "__groupby_" not in rule:
        missing.append(
            {
                "clause": "groupby",
                "detail": "the seed template groups the aggregate per entity; the rule aggregates flat",
            }
        )
    # Only the metric aggregates. MAX/MIN most often appear inside a presence
    # guard, which the null_guard check above already owns; testing them here
    # would report the same dropped clause twice.
    for function in ("COUNT_ALL", "SUM", "AVG"):
        if re.search(rf"\b{function}\s*\(", seed_template, re.I) and not re.search(
            rf"\b{function}\s*\(", rule, re.I
        ):
            missing.append(
                {
                    "clause": function,
                    "detail": f"the seed template applies {function}(...); the rule does not",
                }
            )
    return missing


def seed_slot_coverage(rule: str, seed_template: str) -> list[dict[str, Any]]:
    """Distinct slots in the chosen seed need distinct columns in the rule.

    `S39_campaign_promo_delivered` has three column placeholders plus a literal
    `LC_ACTION_TYPE`, so it needs four distinct columns. One run supplied two,
    and the missing counting column was filled by reusing the filter column as
    `COUNT_ALL(LC_ACTION_TYPE)`. Counting distinct columns catches that whatever
    substitution the agent chose.
    """
    if not rule or not seed_template:
        return []
    known = _known_columns()
    slots = {
        name
        for name in TEMPLATE_PLACEHOLDER_RE.findall(seed_template)
        if "col" in name.strip("{}").lower()
    }
    literals = {
        token for token in IDENTIFIER_RE.findall(seed_template) if token in known
    }
    expected = len(slots) + len(literals)
    actual = len(referenced_columns(rule))
    if actual >= expected:
        return []
    return [
        {
            "clause": "slot_coverage",
            "detail": (
                f"the seed needs {expected} distinct columns "
                f"({len(slots)} placeholder slots + {len(literals)} named), but the rule uses "
                f"{actual}; a column is doing two jobs"
            ),
        }
    ]


SHAPE_COMPARISON_MIN_OVERLAP = 0.5


def production_shape_differences(rule: str, client: str | None, limit: int = 3) -> list[dict[str, Any]]:
    """Compare the rendered rule against production VPs built from the same columns.

    Comparability is measured as column overlap across the whole rule, not on the
    aggregated column alone. Keying on one column made almost every lifecycle
    rule a "neighbour" of every other: a correct negative segment rule was
    compared against the positive per-action-key presence family purely because
    both count `L_AGG_MSISDN`, reported as missing a `Max(...) <> NULL` guard,
    and the agent then added that guard plus an unrelated `ID <> NULL` until the
    warnings reached zero. It had rendered the right rule on its first attempt.

    At a 0.5 Jaccard floor the same rule's neighbours are the non-delivered /
    non-responder families that actually share its shape, and they carry no
    guard, so nothing is reported. This is a cross-check for the agent to weigh,
    and a warning that fires on a correct rule will get "fixed".
    """
    if not client:
        return []
    mine = referenced_columns(rule)
    if not mine:
        return []
    try:
        rows = load_vp_descriptions(client)
    except (OSError, ValueError):
        return []

    shape = _rule_shape(rule)
    matches: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        condition = str(row.get("PARENT_CONDITION") or "").strip()
        name = str(row.get("VIRTUAL_PROFILE_NAME") or "").strip()
        if not condition or not name or condition == rule:
            continue
        theirs = referenced_columns(condition)
        shared = mine & theirs
        if not shared:
            continue
        overlap = len(shared) / len(mine | theirs)
        if overlap < SHAPE_COMPARISON_MIN_OVERLAP:
            continue

        other = _rule_shape(condition)
        differences: list[str] = []
        if other["placeholder_owner_kind"] and shape["placeholder_owner_kind"] != other["placeholder_owner_kind"]:
            differences.append(
                f"production puts ${{operator}} ${{value}} on the {other['placeholder_owner_kind']} "
                f"({other['placeholder_owner']}); this rule puts it on the "
                f"{shape['placeholder_owner_kind']} ({shape['placeholder_owner']})"
            )
        if other["fixed_aggregate_comparisons"] and not shape["fixed_aggregate_comparisons"]:
            differences.append(
                "production keeps a literal aggregate threshold ("
                + "; ".join(other["fixed_aggregate_comparisons"])
                + "); this rule has none"
            )
        missing_guards = other["null_guards"] - shape["null_guards"]
        if missing_guards:
            differences.append(
                "production carries a presence guard this rule drops ("
                + "; ".join(sorted(f"{guard} <> NULL" for guard in missing_guards))
                + ")"
            )
        if not differences:
            continue
        matches.append(
            (
                overlap,
                {
                    "class": "convention",
                    "message": (
                        "a production VP built from these columns has a different shape; weigh it, "
                        "do not copy it — a difference is not automatically a defect"
                    ),
                    "vp_name": name,
                    "shared_columns": sorted(shared),
                    "column_overlap": round(overlap, 2),
                    "parent_condition": condition,
                    "differences": differences,
                },
            )
        )

    matches.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in matches[:limit]]


SPACED_ANCHOR_RE = re.compile(r"\bCurrent(?:Time|Month|Week)\s+[-+]|\bCurrent(?:Time|Month|Week)[-+]\s+")
GROUPBY_AGGREGATE_RE = re.compile(
    r"\b(SUM|COUNT_ALL|AVG|MAX|MIN)\(\s*([A-Za-z][A-Za-z0-9_]*)\s*\)__groupby_([A-Za-z0-9_,]+)"
)


def _column_types() -> dict[str, str]:
    return {row.feature_name: (row.data_type or "").strip().lower() for row in load_kpi_meta()}


def aggregate_shape_errors(rule: str) -> list[dict[str, Any]]:
    """Aggregate shapes that never occur anywhere in production.

    Measured over 713 client VPs (omantel + airtel):

    - COUNT_ALL is applied to identifier-like string columns 170 times and to a
      `categorical` column zero times, though kpi_meta holds 109 categorical
      columns. Counting a two-valued category is meaningless, and one run
      emitted `COUNT_ALL(LC_ACTION_TYPE)` — the promotion filter — because no
      counting column had been chosen at all.
    - `__groupby_X` names a column other than the aggregated one in all 44
      usages. Grouping a count by the very column being counted is not a
      narrower audience, it is a no-op.

    Deliberately NOT checked, because production contradicts the intuition:
    aggregating a column that also carries a filter is normal (54 rules), and
    identifier-style naming holds for only 21 of the 31 counted columns.

    Also rejected: "a grouped rule puts the pair on a selector column". The
    37-vs-4 split looked convincing, but all four exceptions — and the reviewed
    per-product revenue rule — are cases where the request leaves the threshold
    to the marketer, so the aggregate is exactly where the pair belongs. The
    discriminator is whether the request decides the comparison, which
    vp-rendering-rules already covers; keying it on the grouping would warn on
    correct rules, and a warning that fires on correct rules gets "fixed".
    """
    errors: list[dict[str, Any]] = []
    if SPACED_ANCHOR_RE.search(rule):
        # All 660 date anchors across both clients are written without spaces.
        # `CurrentTime - 2DAYS` is a formatting drift the engine should not be
        # asked to tolerate.
        errors.append(
            {
                "class": "render",
                "message": (
                    "date anchors are written without spaces: use CurrentTime-2DAYS, not "
                    "CurrentTime - 2DAYS"
                ),
            }
        )
    types = _column_types()
    for function, column, group_by in GROUPBY_AGGREGATE_RE.findall(rule):
        if column in [part.strip() for part in group_by.split(",")]:
            errors.append(
                {
                    "class": "aggregate",
                    "message": (
                        f"{function}({column})__groupby_{group_by} groups by the column it "
                        "aggregates; no production VP does this. Group by the entity the "
                        "request separates on, such as the runtime selector column."
                    ),
                }
            )
    for function, column in AGGREGATED_PAIRS_RE.findall(rule):
        if function.upper() in {"COUNT_ALL"} and types.get(column) in {"categorical", "numeric", "date"}:
            errors.append(
                {
                    "class": "aggregate",
                    "message": (
                        f"COUNT_ALL({column}) counts a {types.get(column)} column; all 170 "
                        "production COUNT_ALL usages name a string identifier. Choose the column "
                        "naming the entity the request asks you to find, not the category you "
                        "filter on or the measure you would sum."
                    ),
                }
            )
    return errors


def _selector_suggestion(rule: str, client: str | None, request: str = "") -> str:
    """Name the column the client pairs with whatever this rule aggregates.

    Only when the request leaves room for one. "any promotion" scopes the rule
    to the event type alone, and the pair-less form is then the right answer, so
    suggesting a selector there argues against a correct rule.
    """
    if ANY_SCOPE_RE.search(request or "") and not SELECTOR_CUE_RE.search(request or ""):
        return (
            " The request says ANY, which scopes the rule to the event type alone, so the "
            "fixed form is correct here — do not add a selector column."
        )
    if not client:
        return " Confirm the fixed form is what the request asked for."
    from vp_agent.tools.retrieval_index import client_selector_for_counted

    counted = AGGREGATED_COLUMN_RE.findall(rule)
    options: list[tuple[str, int]] = []
    pairings = client_selector_for_counted(client)
    for column in counted:
        options = list(pairings.get(column) or ())
        if options:
            break
    if not options:
        return " Confirm the fixed form is what the request asked for."
    listed = " or ".join(f"{name} ({count} rules)" for name, count in options)
    return (
        f" Production rules aggregating {counted[0]} put ${{operator}} ${{value}} on {listed}; "
        "add one of those unless the request really is fixed."
    )


def validate_rule(
    rule: str,
    request: str,
    table: str | None = None,
    client: str | None = None,
) -> dict[str, Any]:
    errors = []
    warnings = []

    # At most one runtime pair, not exactly one. 22 of the 713 production VPs
    # carry none at all — `COUNT_ALL(SUBSCRIPTIONS_Product_Id) = 0` is a live
    # rule — so requiring one rejects shapes the engine accepts. Worse, the
    # agent cannot simply be rejected: it must satisfy the check to finish, and
    # the only way to add a pair to a fully-specified rule is to delete the
    # literal that made it correct. One run rendered `COUNT_ALL(...) = 3` for
    # "exactly 3 times", was rejected, and replaced the 3 with the placeholders.
    counted_rule = _collapse_control_group_pair(rule)
    op_count = counted_rule.count("${operator}")
    value_count = counted_rule.count("${value}")
    if op_count > 1 or value_count > 1:
        errors.append(
            {
                "class": "render",
                "message": "a VP expression may contain at most one ${operator} and one ${value}",
                "operator_count": op_count,
                "value_count": value_count,
            }
        )
    elif op_count != value_count:
        errors.append(
            {
                "class": "render",
                "message": "${operator} and ${value} are a pair; neither may appear without the other",
                "operator_count": op_count,
                "value_count": value_count,
            }
        )
    elif op_count == 0:
        # Naming the selector the client actually uses turns this from "confirm
        # your choice" into something the agent can act on. One run shipped a
        # pair-less rule with this warning attached and nothing to do about it,
        # because `select_seed` — which carries the same suggestion — was never
        # called.
        warnings.append(
            {
                "class": "render",
                "message": (
                    "this rule has no runtime ${operator} ${value}, so it is fully specified and "
                    "answers exactly one question. That is legal — 22 production VPs are written "
                    "this way — but a selector column carrying the pair makes the rule reusable "
                    "across campaigns."
                    + _selector_suggestion(rule, client, request)
                ),
            }
        )

    if op_count and "${operator} ${value}" not in counted_rule:
        errors.append({"class": "render", "message": "operator/value placeholders must be adjacent"})

    if "__groupby_" in rule:
        aggregate_positions = [rule.find(fn + "(") for fn in ("SUM", "COUNT_ALL", "AVG", "MAX") if fn + "(" in rule]
        groupby_pos = rule.find("__groupby_")
        if aggregate_positions and min(aggregate_positions) > groupby_pos:
            errors.append({"class": "render", "message": "groupby suffix appears before aggregate"})

    unknown_like = []
    known = _known_columns()
    # A single-`$` name is a runtime variable the engine substitutes per
    # subscriber — `$OM_MSISDN`, `$HBB_imeiNumber`, `$L_PROMO_SENT_DATE` — not a
    # kpi_meta column, so scanning it for unknown identifiers reports a column
    # that was never meant to exist.
    scannable = RUNTIME_VARIABLE_RE.sub(" ", rule)
    for token in IDENTIFIER_RE.findall(scannable):
        if token in FUNCTIONS or token in KEYWORDS or token in known:
            continue
        if token.startswith("Current") or token in {"DAYS", "MONTHS", "WEEKS"}:
            continue
        if token.isupper() and "_" in token:
            unknown_like.append(token)
    if unknown_like:
        warnings.append({"class": "column", "message": "identifier-like tokens not found in kpi_meta", "tokens": sorted(set(unknown_like))[:20]})

    unrendered = unrendered_stated_numbers(rule, request)
    if unrendered:
        limit = COUNT_LIMIT_RE.search(request or "")
        if limit and AGGREGATED_COLUMN_RE.search(rule):
            # "at most 2 times", "fewer than 3", "exactly 3" state the
            # comparison outright, so there is nothing to defer. Offering
            # "deferred to ${operator} ${value}" as an acceptable answer is how
            # three runs deleted the number they had extracted correctly.
            message = (
                f"the request states a count limit ({' '.join(limit.group(0).split())}); a stated "
                "limit renders as a literal comparison on the aggregate and cannot be deferred to "
                "${operator} ${value}. Move the runtime pair to a selector column instead."
            )
        else:
            message = (
                "numbers stated in the request do not appear in the rule; confirm each is "
                "intentionally deferred to ${operator} ${value} rather than lost"
            )
        warnings.append({"class": "coverage", "message": message, "numbers": unrendered})

    errors.extend(aggregate_shape_errors(rule))

    warnings.extend(intent_cue_warnings(rule, request))
    warnings.extend(production_shape_differences(rule, client))

    request_terms = set(tokens(request))
    rule_terms = set(tokens(rule))
    coverage_terms = sorted(t for t in request_terms if t in rule_terms)

    return {
        "ok": not errors,
        "table": table,
        "client": client,
        "errors": errors,
        "warnings": warnings,
        "referenced_columns": sorted(referenced_columns(rule)),
        "coverage_terms": coverage_terms,
    }

