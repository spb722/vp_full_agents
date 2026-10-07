# KPI metadata description audit (no source changes)

Source: `/Users/sachinpb/PycharmProjects/Virtual_profile_agent/data/kpi_meta.csv`  
Total KPI rows: 1277

Original `kpi_meta.csv` was **not** modified. This is a review pack only.

## Output files

1. **`kpi_meta_problems_P0_description_measure_20260921.csv`** — start here  
   High-priority description/measure confusion (e.g. revenue text saying “usage”, MOU text saying “revenue”, name vs value_type conflicts).

2. **`kpi_meta_problems_with_corrections_20260921.csv`** — full review list  
   P0 description issues + empty descriptions + revenue-named rows with `value_type=count`.

3. Earlier broad scan (optional): `kpi_meta_description_issues_20260921.csv`

## Counts

| Bucket | Rows |
|---|---|
| P0 description / measure conflicts | 22 |
| Empty descriptions | 142 |
| Revenue-named with value_type=count | 537 |
| **Total in full corrections file** | 701 |

## Priority meanings

- **P0_money_desc_says_usage**: money/revenue KPI description uses the word “usage” (can pull volume requests).
- **P0_volume_desc_says_revenue**: volume/MOU KPI description talks about revenue.
- **P0_name_value_type_conflict**: feature name and value_type disagree on measure.
- **P2_empty_description**: no description text.
- **P1_value_type_count_on_revenue_name**: name/description look like revenue but value_type is count.

## Columns in the CSV

- `current_description` — as in kpi_meta today  
- `suggested_description` — proposed correction for your review (not applied)
