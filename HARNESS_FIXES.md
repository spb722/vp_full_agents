# Harness fixes — issue log

Working notes on failures found while stress-testing the VP agent, and what was
changed for each. Written during the Haiku probe (orchestrator and subagents both
on `claude-haiku-4-5`) on 2026-09-15: running the weaker model surfaces harness
weaknesses that a stronger model papers over.

Each entry records the symptom, the evidence, the root cause, and the change.
Where a fix is unverified, it says so.

---

## 1. A named attribute with no value was treated as a question

**Symptom.** "Find customers who did not get any promotion in the last 4 days,
based on segment name" returned no rule. The agent asked whether "based on
segment name" meant filter, group-by, or ignore.

**Evidence.** The slots sent to `retrieve_columns` carried the promotion filter,
the negation, `operator: "="` and `value: "0"` — all correct — but no role at all
for "segment name". The agent then reported:

> "The retrieval system has no standard segment filter candidates."

It asserted an absence it had never tested. `LC_SEGMENT_NAME` exists (kpi_meta id
2747, `LIFECYCLE_CDR`, *"A field used to identify the customer segment targeted by
a rule"*) and appears in 22 Omantel production rules.

**Root cause.** A phrase like "based on segment name" names a **column** but
states no **value**. The skills had a rule for this, but scoped to the main KPI
only:

> Missing main-KPI threshold alone is NOT a clarification; the main KPI keeps
> `${operator} ${value}` placeholders.

A selector column with an unstated value fell through the gap: not the main KPI,
so the exemption didn't apply, so it read as ambiguity.

**Scale.** Not a promotion-specific case. 100 of the 179 seed templates contain a
`{key_col}` slot. Seed `S40_campaign_promo_absent_by_actionkey` says so
explicitly: *"Covers both action key (`L_ACTION_KEY`) and segment
(`LC_SEGMENT_NAME`) variants — same skeleton when column names are replaced."*

**Fix.**

- `.claude/skills/vp-extraction/SKILL.md` — a named attribute with no stated
  value is a filter predicate with `{"operator": "runtime", "value": null}`, not
  a clarification. "Based on X" selects on X; it is not a `group_by` and does not
  mean "return everyone".
- Same file — `null` was overloaded, so the operator now disambiguates it:
  presence operator → `<> NULL`; `operator: "runtime"` → the runtime pair.
- `vp_agent/orchestrator.py` — the same rule in `ORCHESTRATOR_APPEND`, including
  *"Never report that a column does not exist for a role you did not submit to
  retrieval."*

**Result.** Cases B2 and B3 now match their expected output exactly.

---

## 2. A dropped phrase was invisible to every existing check

**Symptom.** Nothing in the run flagged that "segment name" had vanished.

**Root cause.** `unexplained_phrase_terms` (`vp_agent/tools/retrieve.py`) grades a
role's phrase against the column it matched, so it only ever sees phrases the
agent chose to **submit**. A phrase that never became a role is invisible to it.

**Fix.** `unaddressed_request_terms(request, slots)` in
`vp_agent/tools/retrieve.py` — content words in the request that appear in no
submitted role phrase, filter value, or negation. Window words ("last", "days")
are excluded via `REQUEST_STOP_TERMS` because `time_token` already represents
them and they would otherwise fire on every request.

Wired into the `retrieve_columns` PostToolUse branch in `vp_agent/hooks.py`. For
the failing request it returns `["segment", "name"]`, so the drop can no longer
be silent.

---

## 3. The rendering skill contradicted the fix

Found while implementing #1, not predicted.

**Two problems in `.claude/skills/vp-rendering-rules/SKILL.md`:**

1. The rule for putting the pair on a selector column **already existed**, but
   was gated on the marketer *stating* a count ("at most 4 times"). An implied
   `= 0` from a negation never triggered it.
2. The file's last line read: *"Fixed filter operands never carry the runtime
   pair; `${operator} ${value}` belongs only to the main KPI."* That would have
   told the agent to refuse the new slot outright.

**Fix.** Broadened the trigger to cover implied comparisons — negation → `= 0`,
bare presence → `> 0` — with the reviewed production skeleton as a worked
example, and rewrote the closing rule to distinguish a **fixed** operand (never
carries the pair) from a **runtime selector** (takes the pair instead of the main
KPI).

**Note.** This is the reviewed production convention, confirmed against all 22
`LC_SEGMENT_NAME` rules in `vpdesc-all-omantel.csv`:

```
L_PROMO_SENT_DATE >= CurrentTime-${X}DAYS
AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)
AND LC_SEGMENT_NAME ${operator} ${value}
AND COUNT_ALL(L_AGG_MSISDN) = 0
```

Exactly one runtime pair, on the selector; the aggregate carries a literal. This
settles an earlier open question about relaxing the one-pair invariant: the
invariant was never the problem, the missing rule was *where the pair goes*.

---

## 4. Render-time validation ran against an empty request

**Symptom.** A rendered rule that dropped a negation was not caught at render
time.

**Root cause.** The PostToolUse render branch called
`validate_rule(condition, request=str(tool_input.get("request", "")), ...)`, but
`render_condition`'s input has no `request` key. The value was always `""`, so
every intent cue (negation, per-entity, average, alternation, category value,
scope) was computed against an empty string and could never fire.

**Second half of the same bug.** Fixing the parameter alone changed nothing,
because `validate_rule` returns `ok = not errors` and the hook only reported when
`ok` was false. The cue warnings were being computed and thrown away.

**Fix.**

- `ToolState.request`, set in `run_request` (`vp_agent/orchestrator.py`), so hooks
  validate against the real sentence rather than whatever the agent echoed back
  into a tool argument.
- `vp_agent/hooks.py` — pass `request=state.request`, and surface warnings at
  render time, not only errors.

---

## 5. Claude Agent SDK — tool deferral starved the pipeline

**This is the note to keep.** It is an SDK configuration issue, not a modelling
one, and it cost one run its entire result.

**Symptom.** Request B1 ("got a particular promotion within the last 4 days")
returned no rule and no clarification. The trace shows:

```
Skill(vp-extraction)          ✅
Skill(vp-rendering-rules)     ✅
ToolSearch × 8                ← searched for normalize_slots six times
mcp__vp__* calls              ← zero
```

80 seconds and $0.15 spent loading tool schemas that were never used. Having
given up on tools, the agent wrote the rule in prose — which violates hard
invariant 1 — and invented the column `L_PROMOTION_ID`, which does not exist in
`kpi_meta.csv`. The real column is `L_ACTION_KEY`. The invention was a direct
consequence of `retrieve_columns` never running.

Its *reasoning* was correct: runtime selector, `> 0` on the aggregate, pair on the
selector. Only the column names were guesses.

**Root cause — the SDK distinction that matters.** `ClaudeAgentOptions` has two
separate fields, and they do different jobs
(`claude_agent_sdk/types.py:1729` and `:1740`):

| Field | What it does |
| --- | --- |
| `tools` | "Specify the base set of **available** built-in tools." |
| `allowed_tools` | "Tool names that are **auto-allowed without prompting** for permission." |

`build_options` set `allowed_tools` but left `tools` unset. Unset means the CLI
loads the full Claude Code built-in set; with the 12 `mcp__vp__*` tools on top,
the CLI deferred all of them behind `ToolSearch` — the trace reports
`total_deferred_tools: 31`.

So the pipeline tools were reachable only through an extra fetch step, and a
weaker model got stuck on the fetch half of fetch-then-call.

**Fix.** `vp_agent/orchestrator.py`:

```python
tools=["Skill", "Read", "Agent"],
```

The pipeline needs three built-ins. Narrowing the base set is what keeps the vp
MCP tools directly callable.

**Belt and braces.** `vp_agent/hooks.py` `PreToolUse` now tracks which tools
`ToolSearch` has loaded (`tools_loaded`) versus which have actually been called
(`tools_called`). A repeat search for a loaded-but-never-called tool gets one
retry of grace, then is denied with *"already loaded and never called. Stop
searching and call the tool directly."* B1 would have been stopped at search #3.

**If this needs reverting,** `tools=[...]` is the line to back out first. `Read`
and `Skill` are certain; `Agent` is the name used everywhere else in this codebase
(`allowed_tools`, the hook matchers, the traces), but the verifier subagent is the
thing to watch.

### Other SDK facts confirmed from the installed package

- **Stop hooks can block.** `SyncHookJSONOutput` accepts
  `{"decision": "block", "reason": "..."}`; the docstring notes *"For other hooks,
  only 'block' is meaningful"* (`types.py:553`). `StopHookInput.stop_hook_active`
  (`types.py:350`) is the re-entry guard that prevents an infinite block loop.
- **`skills=` supersedes putting `"Skill"` in `allowed_tools`.** The SDK marks
  that usage deprecated and notes the `skills` option "configures everything
  needed (including allowing the `Skill` tool)" (`types.py:1746`, `:1965`).
  `build_options` currently does both; harmless, but the `allowed_tools` entry is
  redundant.
- **`setting_sources` must include `"project"`** for CLAUDE.md to load
  (`types.py:1953`). It does.

---

## 6. The agent could finish with neither a rule nor a question

**Symptom.** B1 ended with `vp.ok: false` and the message "The agent did not call
`mcp__vp__render_condition`". The caller got nothing actionable — no condition,
and no clarification either.

**Root cause.** The only structural gate was a `PreToolUse` deny that stops
`render_condition` running *too early*. Nothing required the agent to reach render
at all. The `_contains_final_parent_condition` check inspects **tool outputs**
only, so a rule written in the agent's own final message went undetected.

**Fix.** A `Stop` hook in `vp_agent/hooks.py`. If the run is ending and
`render_seen` is false, it blocks once and tells the agent to either finish the
pipeline or finish with exactly one `Clarification question:` line. It intervenes
**exactly once** (`ToolState.stop_blocks`), so a genuine clarification still
terminates.

---

## 7. A data question was asked as if it were a business question

**Symptom.** "Select customers who are non-responders to a bonus for a specified
segment in the last 4 days" returned no rule. The agent asked:

> "In your system, what constitutes a customer 'responding' to a bonus — is this
> a separate response/engagement event, a flag on the bonus record, or …?"

**Evidence.** The whole run was two `Skill` loads and then the question. **Zero
tool calls** — no `normalize_slots`, no `retrieve_columns`, no
`retrieve_existing_vps`, no `select_seed`. 58 seconds, $0.05, no evidence
gathered.

Meanwhile "non-responder" is a **named production family** in
`vpdesc-all-omantel.csv` — about 20 rules, including an exact 4-day match:

```
LC_NONRESPONDER_ACTIONKEY_LAST_4_DAYS
L_BONUS_SENT_DATE >= CurrentTime-4DAYS AND LC_ACTION_TYPE IN LIST (BONUS;Bonus;bonus)
  AND L_ACTION_KEY ${operator} ${value} AND COUNT_ALL(L_AGG_MSISDN) = 0
```

and a segment variant, `LC_NONRESPONDER_LAST_${X}_DAYS`, that is the requested
shape. The term is not ambiguous in this catalog; it is defined.

Note that B3 — "got bonus, based on segment" — matched perfectly. B4 differs only
by the word "non-responders", and that one unfamiliar word stopped the run before
it started.

**Root cause — two layers.**

*Layer 1, the rule.* The prompt's "don't ask before retrieval" rule was scoped to
one narrow case: *"Do not ask clarification for a missing filter **period**
before retrieval."* Nothing forbade asking before any retrieval at all. The agent
even wrote *"this affects which column family and retrieval path I use"* — and
then did not run retrieval.

*Layer 2, the lexical gap.* Even had it called `retrieve_existing_vps`, the match
was weak. `tokens()` splits on non-alphanumerics, so "non-responders" becomes
`non` + `responders`, while the VP name carries the single token `nonresponder`.
The catalog concatenates what a request separates — `NONRESPONDER`, `ACTIONKEY`,
`NONDELIVERED` — so the words a marketer writes were never lexically equal to the
name token meaning the same thing.

**Fix.**

- `vp_agent/orchestrator.py` — a general rule replacing the narrow one: separate
  a **data question** ("does this system record X?", "what does term X mean
  here?") from an **intent question** ("which of two equally supported audiences
  did the marketer mean?"). Tools answer the first; only the user answers the
  second. No clarification may be asked before `retrieve_columns` has run, and an
  unmappable domain term requires `retrieve_existing_vps` first, because client
  VP names encode the client's vocabulary.
- `.claude/skills/vp-extraction/SKILL.md` — the same distinction in
  *Clarification discipline*.
- `vp_agent/hooks.py` — the Stop hook now branches on whether any retrieval ran.
  With no `retrieval_audit_ids`, the block message names the missing evidence
  specifically instead of offering "or just ask", which B4 would have satisfied
  by re-asking.
- `vp_agent/tools/retrieve_vps.py` — `_compound_variants` joins adjacent query
  tokens (and their singular form) so "non-responders" also generates
  `nonresponders` / `nonresponder`. The extra terms enlarge the coverage
  denominator equally for every candidate, so ranking only moves where a compound
  actually hits.

---

## 8. One wrong column silenced every check that would have caught the next error

**Symptom.** "Check whether a promo was sent to a customer in the last 2 days,
confirmed by the sent date" rendered:

```
L_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value}
  AND COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY > 0
```

against an exact production twin, `CHECK_PROMO_LAST_2_DAYS`:

```
L_PROMO_SENT_DATE >= CurrentTime-2DAYS AND L_ACTION_KEY ${operator} ${value}
  AND COUNT_ALL(L_AGG_MSISDN)__groupby_L_ACTION_KEY > 0 AND Max(L_PROMO_SENT_DATE) <> NULL
```

Two defects, and `vp.ok: true` with an empty warnings array. The agent reported
*"✅ No errors or warnings. Rule follows production convention for promo-count
VPs."*

**Defect 1 — the wrong counting column.** `L_AGG_MSISDN` and `L_AGG_CNT` carry
the *same description word for word* in `kpi_meta.csv` ("A non-root profile
MSISDN column used in count VPs"), so metadata cannot separate them. Production
can: **50 rules use `L_AGG_MSISDN`, 4 use `L_AGG_CNT`.** The harness discarded
that ratio — `retrieve.py:84` was binary:

```python
client_prior = 1.0 if row.feature_name in client_prior_columns else 0.0
```

Both scored 1.0 and both were described to the agent as "seen in client
production VPs". The tiebreak then fell to lexical similarity, and the KPI
phrase "promotion **count**" matched `L_AGG_**CNT**`.

**Defect 2 — a dropped seed clause.** The agent selected
`S42_promo_groupby_max`, whose template is

```
{date_col} >= CurrentTime-{N}DAYS AND {key_col} ${operator} ${value}
  AND COUNT_ALL({count_col})__groupby_{key_col} > 0 AND Max({date_col}) <> NULL
```

and rendered the first three clauses only. The fourth is exactly the "confirmed
by the sent date" the request asked for, and the seed is named `..._max`. Nothing
checked that a rendered rule keeps the clauses of the seed it claims to use.

The coverage check was no help: the agent said *"all coverage terms ('date',
'sent') are captured"*, true because both words appear inside `L_SENT_DATE`. The
word survived; the clause did not.

**Why no warning fired — three reasons stacked.**

1. **Defect 1 hid defect 2.** `production_shape_differences` finds comparable
   production rules by the *aggregated* column (`_focus_columns`). Ours aggregates
   `L_AGG_CNT`; the twin aggregates `L_AGG_MSISDN`. No shared column, so the twin
   was skipped entirely.
2. **It could not see this kind of difference anyway.** It detected only two:
   placeholder-owner mismatch, and "production keeps a literal aggregate
   threshold, this rule has none". The rule has `> 0`, so neither applied. A
   missing `<> NULL` guard was not a question it knew how to ask.
3. **It never received the client.** Without one it returns `[]` immediately, and
   the render hook called `validate_rule` without it.

**Fix.**

- `vp_agent/tools/retrieval_index.py` — `client_column_usage(client)` counts how
  many of the client's production VPs use each column (per rule, not per
  mention).
- `vp_agent/tools/retrieve.py` — candidates now carry `production_uses`.
  Reported, not scored, so the choice stays the agent's; `orchestrator.py` tells
  it that when two candidates are interchangeable on description and type, the
  usage count outranks a lexical echo of the KPI phrase in the column name.
- `vp_agent/tools/validate.py` — `seed_clause_regressions(rule, seed_template)`
  reports structural clauses the seed carries and the rule lost (`<> NULL` guard,
  `__groupby_`, a metric aggregate). Column names are compared through the
  template's `{placeholders}`, so only shape is checked. Called from the render
  hook using `ToolState.selected_seed`.
- `vp_agent/tools/validate.py` — `production_shape_differences` gained a
  `null_guards` difference, and a fallback: when the aggregated column matches no
  production rule at all, rescan keyed on the runtime-selector column, so one
  wrong column can no longer silence the whole check.
- `ToolState.client`, set in `run_request`, passed through the render hook — the
  production comparison could not run at render time without it.

---

## 9. Counting the wrong thing, and what production says about aggregates

**Symptom (B1 retest).** The pipeline now ran end to end and picked the right
seed, but counted the filter column:

```
... AND L_ACTION_KEY ${operator} ${value} AND COUNT_ALL(LC_ACTION_TYPE) > 0
                                                        ^ the promotion filter
```

**Root cause — the sentence's subject was dropped.** The request was *"Find
**customers** who got a particular promotion within the last 4 days."* Extraction
wrote `kpi_phrase: "promotion received"` — the event, not the subject. Retrieval
ranks the metric role against that phrase, so it returned promotion columns:

| Rank | Column | Score | type |
|---|---|---|---|
| 1 | LC_ACTION_TYPE | 1.085 | categorical |
| 2 | L_ACTION_KEY | 0.661 | string |

`L_AGG_MSISDN` was not in the top five. The agent then passed `select_seed` only
two columns for a template with three placeholders plus a literal, so one column
filled two jobs.

### What production actually aggregates

Measured over **713 client VPs** (omantel + airtel):

| Function | Usages | Column data types |
|---|---|---|
| `COUNT_ALL` | 170 | string 90, absent-from-kpi_meta 80 — **numeric 0, categorical 0, date 0** |
| `SUM` | 495 | numeric 472, absent 23 — **string 0, categorical 0** |

`kpi_meta` holds 109 categorical columns; production counts none of them.
**COUNT_ALL counts identities, SUM adds numbers**, with no exceptions in 665
usages.

Two further shape facts:

- `__groupby_X` names a column other than the aggregated one in **all 44** usages.
- 26 of the 31 columns production counts are **absent from `kpi_meta` entirely**
  (`RE_REFILL_ID` 23 uses, `LC_ACTION_KEY` 19). Retrieval only knows `kpi_meta`,
  so the agent can never propose them. That is a data gap, not a model gap.

### Rules I tested and rejected

Recording these because two were my own instincts and both were wrong:

- ❌ *"Never aggregate a column you also filter on."* 25 production rules do
  exactly that.
- ❌ *"Counted columns are identifier-named."* Only 21 of 31; production also
  counts `RE_REFILL_TYPE`, `CATEGORY_CATEGORY_NAME`, `UTG_Seg_Type`.
- ❌ *"A grouped rule puts the runtime pair on a selector column."* The 37-vs-4
  split looked convincing, but all four exceptions — and the reviewed
  per-product-revenue rule — are cases where the request leaves the threshold to
  the marketer, so the aggregate is exactly where the pair belongs. Keying it on
  the grouping would warn on correct rules, and issue #8 showed what happens to a
  correct rule that gets warned about.

**Fix.**

- `vp_agent/tools/validate.py` — `aggregate_shape_errors`: `COUNT_ALL` over a
  `categorical` column, and `__groupby_X` where X is the aggregated column, are
  now **errors**. Both are zero-occurrence shapes in production.
- `vp_agent/tools/retrieve.py` — `_role_gates` blocks a categorical candidate
  from the metric role under `COUNT`/`COUNT_ALL`, so the filter column can no
  longer rank first for "what should I count?".
- `vp_agent/tools/validate.py` — `seed_slot_coverage`: the rendered rule must use
  at least as many distinct columns as the chosen seed has distinct column
  placeholders plus named literals. Catches one column doing two jobs.
- `.claude/skills/vp-extraction/SKILL.md` — carry the sentence's subject into
  `kpi_phrase`, plus the COUNT_ALL/SUM rule of thumb.

**Verified:** the two new errors fire on zero of 713 production rules, and on
both failing retest rules. After the gate, B1's metric list contains no
categorical column and surfaces `L_AGG_MSISDN` with `production_uses: 50`.

**Honest limit:** rewriting `kpi_phrase` to keep the subject did *not* lift
`L_AGG_MSISDN` up the ranking — it moved 4th to 5th. Its description ("A non-root
profile MSISDN column used in count VPs") shares no words with the request, so
lexical scoring cannot find it. The deciding evidence is `production_uses`
(50 vs 4) plus the hard error on the wrong choice, not the phrase wording.

---

## 10. A correct rule edited until the warnings went quiet

**Symptom (B2 retest).** Input: *"Select customers who have not received a
promotion in the last 4 days, based on the segment name."* The run rendered four
times. Render #1 was **the expected answer**:

```
L_SENT_DATE >= CurrentTime-4DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)
  AND LC_SEGMENT_NAME ${operator} ${value} AND COUNT_ALL(L_AGG_MSISDN) = 0
```

`validate_rule` returned `ok: true, errors: 0` — and three `convention`
warnings, all identical:

```
[convention] vp=CHECK_PROMO_LAST_2_DAYS  shared=['L_AGG_MSISDN']
   * production carries a presence guard this rule drops (Max(L_PROMO_SENT_DATE) <> NULL)
```

What followed:

```
render #2  ... AND Max(L_SENT_DATE) <> NULL
render #3  ... AND Max(L_PROMO_SENT_DATE) <> NULL     (copied production's text)
render #4  ID <> NULL AND ...                          (a second borrowed clause)
           -> validate: warnings 0
```

It drove the warning count to zero and shipped a rule that answers a different
question, with two date columns in it (`L_SENT_DATE` bound, `L_PROMO_SENT_DATE`
guarded).

**Root cause — "comparable" was defined as one shared column.** The check I added
in #8 found production neighbours via the aggregated column alone. B2's rule
counts `L_AGG_MSISDN`; so do 50 production rules. It was therefore compared
against `CHECK_PROMO_*` — a **positive, per-action-key, grouped presence** family
— while B2 is a **negative, per-segment, ungrouped** rule. The only thing they
share is the counted column.

Ranking by whole-rule column overlap puts the right family first:

| Neighbour | Jaccard | shared |
|---|---|---|
| `LC_NONRESPONDER_LAST_*` / `LC_NONDELIVERED_LAST_*` | **0.60** | LC_ACTION_TYPE, LC_SEGMENT_NAME, L_AGG_MSISDN |
| `CHECK_PROMO_LAST_2_DAYS` | 0.17 | L_AGG_MSISDN |

And the right family carries **no** `Max(...) <> NULL`, so nothing should have
been reported at all.

**Contributing cause — the warning contract read as an order.** The rendering
skill said warnings "must be addressed", with `convention` described as "adopt
the production convention or say why this request differs". The agent adopted.

**Fix.**

- `vp_agent/tools/validate.py` — `production_shape_differences` now measures
  comparability as Jaccard overlap of all referenced columns with a 0.5 floor,
  ranks by overlap, and reports `column_overlap` on each finding. The
  aggregated-column keying and the selector fallback from #8 are both gone; the
  overlap measure subsumes them.
- Its message now reads *"weigh it, do not copy it — a difference is not
  automatically a defect"*.
- `.claude/skills/vp-rendering-rules/SKILL.md` — `convention` is explicitly
  INFORMATION, not a defect report: *"Never add a clause you cannot trace back to
  the request just to make this warning stop"*, plus *"Zero warnings is not the
  goal and is not evidence of a better rule"*, with this run as the example.

**Verified.**

```
B2 render #1 (the correct rule):   3 convention warnings  ->  0
B5 (genuinely wrong shape):        still warned, pointing at L_PROMOCHECK_DATE
                                   "production puts ${operator} ${value} on the column (L_ACTION_KEY)"

production rules that warn against their own peers:
   old keying (shares the aggregated column):  110/602  (18%)
   new keying (>=0.5 column overlap):           71/602  (12%)
```

**Note on the seed resolver.** B2's `select_seed` proposed
`S39_campaign_promo_delivered` — the **positive** `> 0` seed — for a negation,
and suggested `key_col` and `count_col` as the *same* column (`L_AGG_MSISDN`).
The agent overrode both correctly. Not fixed here, but the resolver proposing one
column for two distinct slots is the same defect `seed_slot_coverage` (#9) now
catches downstream.

---

## 11. One column filling two seed slots

**Symptom (B5 retest).** Input: *"Check whether a promo was sent to a customer in
the last 2 days, confirmed by the sent date."* The rendered rule was:

```
L_SENT_DATE >= CurrentTime-2DAYS AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)
  AND COUNT_ALL(L_AGG_MSISDN)__groupby_L_AGG_MSISDN ${operator} ${value}
  AND Max(L_SENT_DATE) <> NULL
```

`L_ACTION_KEY` is absent, the grouping names the counted column, and the `> 0`
the request implies is gone.

**Root cause is one line of resolver output.** `select_seed` chose the right seed,
`S42_promo_groupby_max`, whose template uses `{key_col}` twice — as the selector
and as the grouping — and `{count_col}` once:

```
{date_col} >= CurrentTime-{N}DAYS AND {key_col} ${operator} ${value}
  AND COUNT_ALL({count_col})__groupby_{key_col} > 0 AND Max({date_col}) <> NULL
```

It then suggested:

```json
{"N": 2, "count_col": "L_AGG_MSISDN", "date_col": "L_SENT_DATE", "key_col": "L_AGG_MSISDN"}
                       ^^^^^^^^^^^^                                ^^^^^^^^^^^^  same column
```

Substituting that gives `COUNT_ALL(L_AGG_MSISDN)__groupby_L_AGG_MSISDN` directly.
The agent then dropped the now-degenerate `{key_col} ${operator} ${value}`
predicate and moved the pair onto the aggregate, which pushed out the `> 0`. One
bad substitution produced four of the five differences.

`_suggest_variables` called `_infer_column` once per role against the same
candidate pool with nothing excluded, so two roles could resolve to one column.
B2's run shows the same suggestion (`key_col` = `count_col` = `L_AGG_MSISDN`);
there the agent overrode it.

**Three layers underneath, each found while fixing the one above.**

1. *Nothing excluded already-assigned columns.* Fixed by threading a `taken` set.
2. *The type preferences were dead code.* The agent passes columns as
   `{feature_name, group_name}` with no `data_type`, so every predicate tested
   `""` and each role fell through to "first in the list". Types now come from
   `kpi_meta` when the caller omits them.
3. *Type still cannot separate two string identifiers.* `L_AGG_MSISDN` and
   `L_ACTION_KEY` are both `string`, so the choice came down to list order — and
   alphabetical role ordering meant `count_col` consumed the selector before
   `key_col` was considered.

**What separates them is the role production uses them in**
(`client_role_usage`, measured per client):

| Column | aggregated | pair_owner | groupby |
|---|---|---|---|
| `L_AGG_MSISDN` | **50** | 0 | 0 |
| `L_ACTION_KEY` | 2 | **34** | 10 |
| `LC_SEGMENT_NAME` | 0 | **21** | 0 |
| `LC_ACTION_TYPE` | 0 | 0 | 0 |

`LC_ACTION_TYPE` scores zero everywhere because it is always a filter — which is
also why counting it (#9) was wrong.

**Fix.**

- `vp_agent/tools/retrieval_index.py` — `client_role_usage(client)` counts each
  column's appearances as `aggregated`, `pair_owner` and `groupby`.
- `vp_agent/tools/seed.py` — `_infer_column` now picks the candidate production
  most often uses *in that role*, falling back to type rules; `_suggest_variables`
  excludes already-assigned columns and fills roles in a meaningful order
  (`ROLE_FILL_ORDER`) rather than alphabetically.
- `vp_agent/tools/seed.py` — a role is left **unfilled** rather than filled with
  a column `validate_rule` would reject. A missing variable surfaces as an
  adaptation the agent can act on; a bad one substitutes silently.

**Verified.**

```
B5 with the right columns supplied:  key_col=L_ACTION_KEY   count_col=L_AGG_MSISDN   (expected shape)
B2 segment case:                     key_col=LC_SEGMENT_NAME count_col=L_AGG_MSISDN  (expected shape)
B1:                                  key_col=L_ACTION_KEY   count_col unfilled       (none was supplied)
```

**Not fixed — the agent under-supplied.** B5 handed `select_seed` only
`L_AGG_MSISDN` and `LC_ACTION_TYPE`, though retrieval had ranked `L_ACTION_KEY`
first among metric candidates. The resolver can only choose from what it is
given, so B5 still needs the agent to pass the selector column. `seed_slot_coverage`
(#9) does **not** catch this: the rendered rule happened to use three distinct
columns, which matches the template's three placeholders — just not the right
three. What catches it today is the `__groupby_` self-reference error from #9.

---

## 12. A count threshold read as a column type (B5, second retest)

**Symptom.** The pipeline ran to completion and the date column was right this
time, but:

```
L_PROMO_SENT_DATE <> NULL AND L_PROMO_SENT_DATE >= CurrentTime - 2DAYS
  AND LC_ACTION_TYPE IN LIST (Promotion;PROMOTION;promotion)
  AND L_ACTION_KEY ${operator} ${value} AND COUNT_ALL(L_AGG_CNT) > 0
```

vs expected `... COUNT_ALL(L_AGG_MSISDN)__groupby_L_ACTION_KEY > 0 AND
Max(L_PROMO_SENT_DATE) <> NULL`. Final validation: **0 errors, 0 warnings.**

The agent called `retrieve_columns` **six times**, rewording `kpi_phrase` each
time, and `L_AGG_MSISDN` appeared in none of the six candidate lists.

**Root cause — `operator`/`value` were read as a constraint on the column.**
`retrieve_columns` set `numeric_threshold` from the comparison operator:

```python
numeric_threshold = operator in {">", ">=", "<", "<="} or value.isdigit()
```

For `COUNT_ALL(x) > 0` the `> 0` constrains the **count**, not `x`. Every
`*_count` column was boosted and every string identifier penalised, so the page
filled with `OG_Total_SMS_count`, `Recharge_count`, `COMMON_OG_CALL_COUNT`.
`L_AGG_MSISDN` sat at **rank 23**.

**Why the ranking could not recover on its own.** `L_AGG_CNT` and `L_AGG_MSISDN`
have *byte-identical* metadata in `kpi_meta` — same description, type, group,
kpi_type. The 0.09 score gap between them is an artifact of feature-name length
in BM25, not signal. No amount of lexical tuning can separate them; only how
production uses each one can (counted 4 times vs 50).

**Fix.**

- `vp_agent/tools/retrieve.py` — a COUNT aggregate no longer sets
  `numeric_threshold`; instead it applies the mirror-image preference, since what
  gets counted is an identifier.
- `vp_agent/tools/retrieve.py` + `validate.py` — the count gate now covers
  `numeric` and `date` as well as `categorical`, in both the retrieval role gate
  and `aggregate_shape_errors`. All 170 production `COUNT_ALL` usages name a
  string identifier; numeric, categorical and date each occur zero times.
- `vp_agent/tools/seed.py` — `count_col` no longer falls back to a `date`
  column. The resolver had suggested
  `{"count_col": "L_PROMO_SENT_DATE", "key_col": "L_AGG_CNT"}`.
- `vp_agent/tools/validate.py` — `CurrentTime - 2DAYS` is now an error. All 660
  date anchors across both clients are written without spaces.

**Verified.**

```
metric candidates for "count of customers" + COUNT_ALL, before:
   OG_Total_SMS_count(0)  Recharge_count(0)  L_AGG_CNT(4)  COMMON_OG_CALL_COUNT(3) ...
after:
   L_AGG_CNT(4)  L_AGG_MSISDN(50)  COUNT_OF_SUBSCRIPTION_PID(0)  LC_SEGMENT_NAME(21) ...

new errors on 713 production rules: 0 false positives
```

The agent now sees the two identical-metadata columns side by side with `4` and
`50` beside them, which is the only evidence that separates them.

**Still open from this run.**

- **The groupby warning was delivered and ignored.** `seed_clause_regressions`
  did fire *"the seed template groups the aggregate per entity; the rule
  aggregates flat"* through the render hook. Hook context is not captured in
  Langfuse, so whether the agent read it cannot be told from the trace.
- **Six `retrieve_columns` calls.** `ORCHESTRATOR_APPEND` says to call it once;
  nothing enforces that, and re-querying with a reworded phrase is how the run
  drifted from "promo sent" to "count of customers".
- **The extra `LC_ACTION_TYPE` filter and `L_PROMO_SENT_DATE <> NULL`** were the
  agent's own additions, neither requested nor warned about.
- **`verifier_ran: false`** again.

---

## 13. A stated count limit deleted to make room for a placeholder (C1, C2, C5)

**Symptom.** Three requests that state a hard count:

```
C1  "received a promotion at most 2 times in the last 7 days"   expected COUNT_ALL(...) <= 2
C2  "received fewer than 3 bonuses in the last 30 days"         expected COUNT_ALL(...) <  3
C5  "received a promotion exactly 3 times today"                expected COUNT_ALL(...) =  3
```

All three rendered `COUNT_ALL(L_AGG_MSISDN) ${operator} ${value}` — the number
gone, the pair on the aggregate.

**Extraction was correct in all three** (`operator: "<=", value: "2"` etc.), and
C5's *first* render was `COUNT_ALL(L_AGG_MSISDN) = 3`, which is right. The second
render replaced it with the placeholder pair.

**Root cause chain.**

1. All three handed `select_seed` the same two columns —
   `['L_AGG_MSISDN', 'LC_ACTION_TYPE']` — with no selector, even though C2's
   retrieval had returned `L_BONUS_ACTION_KEY` and `L_BONUS_SENT_DATE` in the
   filter role.
2. The seed template needs a selector: `{key_col} ${operator} ${value}`. With
   none supplied, the resolver put the **metric** column in the selector slot
   (`key_col: L_AGG_MSISDN`) and left `count_col` empty.
3. With the pair therefore on the aggregate, the "exactly one `${operator}` and
   one `${value}`" invariant forced the literal out. C2's render #1 tried
   `COUNT_ALL(...) < ${value}` — keeping the operator, deferring only the number
   — and the invariant rejected that too.
4. The `coverage` warning fired every time with the dropped number, and was
   ignored every time, because its own wording offered the escape: *"confirm each
   is intentionally deferred to `${operator} ${value}`"*.

**The client's data already answers step 2.** Among production rules counting
`L_AGG_MSISDN`, the runtime pair sits on `L_ACTION_KEY` (29) or
`LC_SEGMENT_NAME` (21) — never on `L_AGG_MSISDN` itself, which is counted 50
times and is a selector zero times.

**Fix.**

- `vp_agent/tools/seed.py` — a column whose production role is *being counted*
  can no longer fall into an identifier slot. The slot is left empty instead.
- `vp_agent/tools/retrieval_index.py` — `client_selector_for_counted(client)`
  maps each counted column to the selectors production pairs it with.
- `vp_agent/tools/seed.py` — when an identifier slot is unfilled, the proposed
  seed now carries a `selector_hint` naming those columns with their counts, so
  the empty slot comes with a way to fill it.
- `vp_agent/tools/validate.py` — `COUNT_LIMIT_RE` detects a stated limit ("at
  most 2", "fewer than 3", "exactly 3") and the `coverage` warning then says the
  limit **cannot** be deferred. An unstated threshold keeps the old wording.
- `.claude/skills/vp-rendering-rules/SKILL.md` — the same distinction, plus the
  instruction to use `selector_hint` rather than surrendering the aggregate.

**Verified.**

```
resolver, C2's actual columns
   before:  {'N': 30, 'key_col': 'L_AGG_MSISDN', 'date_col': 'L_SENT_DATE'}
   after:   {'N': 30, 'count_col': 'L_AGG_MSISDN', 'date_col': 'L_SENT_DATE'}
            + selector_hint: L_ACTION_KEY (29 rules), LC_SEGMENT_NAME (21 rules)

resolver, with the selector supplied (unchanged, already correct)
   {'key_col': 'L_BONUS_ACTION_KEY', 'count_col': 'L_AGG_MSISDN', ...}

coverage warning
   C1 -> "the request states a count limit (at most 2) ... cannot be deferred"
   C2 -> "the request states a count limit (fewer than 3) ... cannot be deferred"
   C5 -> "the request states a count limit (exactly 3) ... cannot be deferred"
   "greater than a specified value" -> no coverage warning (correctly unchanged)
```

**Open decision.** All of this exists to satisfy the "exactly one
`${operator} ${value}`" invariant while keeping the stated number. **22 of the
713 production rules carry no runtime pair at all**, so the engine accepts
pair-less rules and the invariant is ours, not the engine's. Under a
"at most one pair" rule, C5's first render — `COUNT_ALL(L_AGG_MSISDN) = 3` —
would simply have been accepted. Left as-is because every expected output in this
batch carries a selector.

---

## 14. "Exactly one runtime pair" relaxed to "at most one"

**The decision behind #13.** Issues 1–13 all worked around a rule the engine
never had.

**Evidence.** 22 of the 713 production VPs carry no `${operator} ${value}` at
all, and they are live rules:

```
CountofSUBSCRIPTIONS_Product_Id   COUNT_ALL(SUBSCRIPTIONS_Product_Id) = 0
dpi_total_usage_month             dpi_app_usage_event_date = CurrentMonth AND SUM(dpi_app_usage_usage) > 0
I_WHITELIST_VP                    I_RULE_ID = ${SCHEDULE_ID}
```

The last one has a runtime placeholder, just not the `${operator} ${value}`
pair — so the domain rule is "a VP *may* have a runtime knob", and the harness
had written "a VP *must* have exactly one, of this specific kind".

**Why it mattered more than a normal false rejection.** A validation rule the
agent must satisfy to finish is not a filter, it is a force. C5 rendered
`COUNT_ALL(L_AGG_MSISDN) = 3` for *"exactly 3 times"* — correct — was rejected
for having zero pairs, and the only way to comply was to delete the `3`:

```
render #1   ... AND COUNT_ALL(L_AGG_MSISDN) = 3                      correct, rejected
render #2   ... AND COUNT_ALL(L_AGG_MSISDN) ${operator} ${value}     legal, wrong
```

The check did not catch a bug; it manufactured one.

**Two checks were involved, not one.** `op_count != 1 or value_count != 1`
rejected zero pairs, and `"${operator} ${value}" not in rule` — the adjacency
check — rejected them a second time.

**Fix** (`vp_agent/tools/validate.py`), severity matched to what is actually
impossible:

| Shape | Before | After |
| --- | --- | --- |
| two pairs | error | **error** — a genuine rendering bug |
| one half of a pair | error | **error** — `< ${value}` alone is malformed |
| split, non-adjacent pair | error | **error** |
| exactly one pair | ok | ok |
| **zero pairs** | **error** | **warning** — legal, but say why the fixed form was chosen |

The adjacency check now runs only when a pair is present.

**Verified.**

```
C5's first render:                       ok=True, with the new render warning
two pairs / half pair / split pair:      still rejected
the 22 pair-less production rules:       0 still rejected  (was 22)
```

`.claude/skills/vp-rendering-rules/SKILL.md` carries the same rule, including:
*"NEVER delete a stated literal in order to create a pair."*

**Relationship to #13.** Fixes 1–3 there are now belt-and-braces rather than
load-bearing: they steer the agent toward the reusable selector form, which is
still preferred, but the fully specified form no longer has to be destroyed when
no selector exists.

---

## 15. The control-group pattern the harness could not see (C3, C4)

**Symptom.** Neither request produced a rule.

```
C3  "Find customers who received a promotion or its control-group variant."      -> clarification
C4  "Find customers who did not receive a bonus or its control-group variant."   -> clarification
```

Both asked what a control group is:

> *"is that recorded as a value like 'Control' in the LC_ACTION_TYPE column, or in
> a separate treatment/variant assignment column?"*

**Three independent failures, any one of which alone would have sunk it.**

**1. The validator rejected the correct answer.** The expected shape repeats the
runtime pair with a `_CG` suffix:

```
(L_ACTION_KEY ${operator} ${value} OR L_ACTION_KEY ${operator} ${value}_CG)
  AND COUNT_ALL(L_AGG_CNT)__groupby_L_ACTION_KEY > 0
```

Textually that is two `${operator}` and two `${value}`, which #14's "at most one
pair" rule rejects. But it is **one** runtime value used twice: the marketer
supplies one action key and the rule also matches that key's control-group twin.
Four production VPs are written this way — `L_AK_PROMO_COUNT`,
`L_AK_BONUS_COUNT`, `L_AK_PROMO_COUNT_3D`, `L_PROMO_DT` — and they are the only
rules in the corpus with more than one pair:

```
${operator} count per production rule:  {0: 22, 1: 687, 2: 4}
```

**2. Retrieval could not reach the pattern.** The agent did call
`retrieve_existing_vps` — the #7 fix working — but got nothing useful:

```
query "control group variant treatment assignment"
   -> AUDIENCE_SEGMENT_NAME, M3_STREAMING_SESSION_COUNT, M2_STREAMING_SESSION_COUNT ...
```

None of the four defining rules has "control", "group" or "variant" in its name;
the convention lives in the condition as `${value}_CG`, and the only token is
"cg". The marketer's words and the data's token had no bridge — the same shape as
the "non-responder" vs `NONRESPONDER` gap in #7, but an abbreviation rather than a
compound. `kpi_meta` contains the word "control" zero times.

**3. So it asked, and never reached `select_seed`.** Which is the sharpest part:
**four seeds already encode this pattern** — `S44_action_key_or_cg_absent`,
`S45_action_key_or_cg_present`, `S46` and `S47` for the time-scoped forms. Given
C3's own slots, `select_seed` proposes the family immediately. It was never
called.

**Fix.**

- `vp_agent/tools/validate.py` — `_collapse_control_group_pair` normalises
  `(COL ${operator} ${value} OR COL ${operator} ${value}_CG)` to a single pair
  before counting. The regex backreferences the column, so two *different*
  columns remain an error.
- `vp_agent/tools/retrieval_index.py` — `SYNONYMS` now bridges
  `control` → `cg` and `cg` → `control`/`group`.
- `.claude/skills/vp-rendering-rules/SKILL.md` — a `Control-group variants (_CG)`
  section with the shape, the four production rules, and the seed ids.
- `.claude/skills/vp-extraction/SKILL.md` — "or its control-group variant" is a
  known pattern, not a clarification; look it up before asking.

**Verified.**

```
C3 expected answer:   ok=False -> ok=True
C4 expected answer:   ok=False -> ok=True
two different columns in the same shape:   still rejected
two unrelated pairs:                       still rejected
713 production rules, placeholder errors:  0

the agent's own failing query, "control group variant treatment assignment":
   before -> AUDIENCE_SEGMENT_NAME, M3_STREAMING_SESSION_COUNT, ...
   after  -> L_AK_PROMO_COUNT_3D, L_AK_PROMO_COUNT, L_PROMO_DT, L_AK_BONUS_COUNT
```

**Note on seed ranking.** For C3 ("received" → present) `select_seed` proposes
`S44_..._absent` rather than `S45_..._present`, and for C4 (a negation) it
proposes the generic `S137` with `S44` only third. The family is reachable but
the positive/negative axis is not ranked on. Not fixed here.

---

## Open items

**Not yet resolved — needs a decision.** Two column choices differ between the
expected outputs and production, and both block scoring the test cases:

- `L_SENT_DATE` vs `L_PROMO_SENT_DATE` — expected outputs use both; all 22
  `LC_SEGMENT_NAME` production rules use `L_PROMO_SENT_DATE`. Parked earlier as
  "a configuration that can be changed anytime"; it has now recurred five times,
  and production itself is inconsistent: the non-responder family uses
  `L_BONUS_SENT_DATE` for its action-key variant but `L_PROMO_SENT_DATE` for its
  segment variant, on rules that are otherwise identical.
- `L_AGG_CNT` vs `L_AGG_MSISDN` — same split; production uses `L_AGG_MSISDN`
  throughout.

**Unverified.** None of the tests added for these fixes have been executed —
`Bash` is denied by project settings, so they were traced by hand only. Run:

```
python -m pytest tests/test_core_tools.py \
  -k "unaddressed or tool_search or stop_hook or compound or nonresponder \
      or production_usage or seed_clause or convention_check"
```

**Not in scope yet.** B5 also recorded its columns as
`[L_SENT_DATE, LC_ACTION_TYPE, L_AGG_CNT]` while the rendered rule contains
`L_ACTION_KEY` and never mentions `LC_ACTION_TYPE`. The resolution record and the
rule can disagree and nothing checks that they agree.

---

## Recurring theme

Most of these are the same shape: **the harness made a decision by deletion or
omission that belonged to the agent, and nothing measured the loss.**

- A phrase dropped at extraction, with no check that every part of the request
  reached a role (#1, #2).
- A rule that existed but whose trigger was too narrow to fire, plus a second
  rule that flatly contradicted it (#3).
- Warnings computed and discarded (#4).
- Tools present but unreachable (#5).
- A run allowed to end having produced nothing (#6).
- A question asked of the user that the tools already answered, because no rule
  distinguished a data question from an intent question (#7).
- Evidence flattened to a yes/no, and a cross-check keyed so narrowly that the
  first error hid the second (#8).

The corresponding discipline: when the harness removes an option, make the
removal **visible** to the agent rather than silent, and add a deterministic check
that the request's content survived the pipeline.
