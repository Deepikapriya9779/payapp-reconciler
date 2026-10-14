# payapp-reconciler — Build Plan

> **Status:** built (milestones M0 to M7 are in the git history). Two additions came up during the
> build: an optional `approved_date` column in `change_orders.csv`, so a historical application is
> reviewed against the change orders approved as of its period end, and an optional
> `project.json` for the project and GC names. The certifiable G703 lines were built in M2,
> because rule R06 measures the retainage threshold on certifiable progress.

Automated review of subcontractor pay applications (AIA G702/G703 progress billing) for GCs.

**Core principle:** every number is computed deterministically in Python with `Decimal`. The LLM
only drafts the plain-English memo from structured findings. It never does math and never decides
what is certifiable.

---

## 1. Domain interpretations to confirm

Each of these is a judgment call where the brief could be read more than one way. I've picked a
default for each, and I'd like your OK (or a correction) before building.

| # | Topic | Proposed interpretation | Why |
|---|-------|------------------------|-----|
| D1 | **Column D "From Previous Application"** | D must equal the prior certificate's **D + E** (work completed). It does **not** include stored materials (F). When stored materials get installed, they move into E in the period they're installed. | That's the AIA G703 column header, "From Previous Application (D + E)". The brief says D is "the total the GC certified". Taken literally, that would include F, and a construction reviewer would catch the mistake. |
| D2 | **Retainage reduction** | When certifiable total completed & stored ÷ contract sum to date ≥ threshold, the reduced rate applies to **all** work to date, which releases the difference. The % complete comes from **certifiable** figures, not claimed ones. | This is the most common subcontract wording. Using certifiable figures means a sub can't over-bill past 50% to trigger an early release. Real contracts often need GC/owner/surety consent. The tool flags the mismatch, and the PM decides. |
| D3 | **Retainage on stored materials** | One rate covers completed work and stored materials. The G702 JSON carries a single line 5 (total retainage = Σ column I). | The G702 form allows "5a + 5b **or** Total in Column I of G703". Separate 5a/5b rates go on the roadmap. |
| D4 | **Certifiable policy** | **Trusted:** C (SOV + approved COs) and D (prior certificate). **Accepted as claimed:** E and F, because field-verifying progress is the PM's job and the tool can't see the jobsite. Then: G is capped at C, unapproved or unknown CO lines certify at $0, duplicate rows count once, and retainage uses the correct rate. Line 7 = prior certificate's line 6. | Rebuilds the payment from sources the GC controls. If a sub carries forward an amount the GC cut last month, it can't sneak back in through D. If the work is now done, the sub bills it in E. |
| D5 | **Change-order lines** | A CO can **adjust an existing SOV line** (its `sov_line_no` is in `sov.csv`) or **add its own G703 line** (by convention, an item number like `CO-03`). A `CO-*` line with no approved CO behind it is billing against an unapproved or unknown CO. | Both practices are common. Most GCs make subs carry each CO as its own line. |
| D6 | **Front-loading defaults** | Applies to lines whose description matches `general conditions`, `supervision`, or `project management`. A line is flagged when its % complete exceeds the sub's overall % complete (excluding that line) by more than **25 points** (configurable with `--front-load-threshold`). **Mobilization and bonds/insurance are left out by default** because they're legitimately billed up front. | General conditions should track time or progress. Flagging mobilization would produce false positives a PM would ignore. |
| D7 | **Sign convention** | Variance = claimed − certifiable. Positive means the sub over-claimed. Negative means the GC owes more, e.g. a missed retainage reduction. Each finding's `dollar_impact` uses the same sign: claimed value − correct value. | The tool is neutral and reports under-billing too. |
| D8 | **Tolerance and rounding** | Money is rounded to cents with `ROUND_HALF_UP` in one helper. Comparisons allow $0.01 of rounding slack. Retainage is computed **per line** and then summed. % complete is a percent with 2 decimals (`45.23`). | This matches how G703 spreadsheets are built. |
| D9 | **G702 tie-out localization** | Source lines are compared to their sources: 1 → subcontract, 2 → CO log, 4 → Σ G, 5 → Σ I, 7 → prior certificate. Derived lines are checked against the sub's **own** upstream lines: 3 = 1 + 2, 6 = 4 − 5, 8 = 6 − 7, 9 = 3 − 6. | A single typo is flagged once, on the line where it happened, instead of cascading into 4 findings. |

---

## 2. Repository layout

```
payapp-reconciler/
├── pyproject.toml            # hatchling, console script `payapp-reconcile`, ruff + pytest config
├── README.md  LEARNING.md  LICENSE (MIT)  .env.example  .gitignore
├── docs/DESIGN.md
├── .github/workflows/ci.yml  # py3.11 + 3.12: ruff check, ruff format --check, pytest + coverage gate
├── scripts/generate_sample.py
├── data/sample/              # generated, committed: the Desert Ridge MOB project
│   ├── subcontracts.csv  sov.csv  change_orders.csv
│   ├── certified/<sub>_app_<n>.csv        # apps 1-4
│   ├── submitted/<sub>_app_<n>.csv        # apps 1-5
│   ├── submitted/<sub>_app_<n>_g702.json
│   └── expected_findings.json             # answer key
├── src/payapp_reconciler/
│   ├── money.py      # to_cents(), pct(), parse_money() (accepts "$1,234.56"). The only rounding helper.
│   ├── models.py     # pydantic v2: input rows, G702, Finding, ReviewConfig, CertifiableResult
│   ├── loaders.py    # CSV/JSON → models; LoadError names file + row; load_review(project, sub, app)
│   ├── contract.py   # trusted contract lines (SOV + approved COs), retainage_rate(), line classification
│   ├── certify.py    # certifiable G703 lines + G702 rebuilt from trusted inputs; claimed vs certifiable
│   ├── rules.py      # 10 rule functions + RULES registry + run_rules()
│   ├── excel.py      # openpyxl review workbook (Summary / Line Review / Findings)
│   ├── memo.py       # template memo + Anthropic LLM memo with a numeric guardrail
│   └── cli.py        # argparse + rich
└── tests/
    ├── conftest.py               # small in-memory builders (no file I/O for rule tests)
    ├── test_money.py  test_loaders.py  test_contract.py
    ├── test_rules.py             # ≥2 tests per rule: passes clean, catches the error
    ├── test_certify.py
    ├── test_answer_key.py        # every planted error caught; clean apps → zero errors
    ├── test_sample_regenerates.py# generator output == committed data/sample
    ├── test_excel_golden.py      # key cells vs tests/golden/*.json (UPDATE_GOLDEN=1 to refresh)
    ├── test_memo.py              # template path + LLM path with a fake client (never the network)
    └── test_cli.py
```

There are deliberately no frameworks beyond pydantic, openpyxl, rich, and anthropic. The CLI uses argparse.

---

## 3. Data model (pydantic v2, money = `Decimal`)

**Inputs**
- `Subcontract`: sub_id, sub_name, trade, original_contract_sum, retainage_pct,
  retainage_reduced_pct | None, retainage_reduction_threshold_pct | None
- `SovLine`: sub_id, line_no, description, scheduled_value
- `ChangeOrder`: co_no, sub_id, description, amount, status: `approved|pending|rejected`, sov_line_no
- `G703Row` (same model for submitted and certified): item_no (A), description (B),
  scheduled_value (C), from_previous (D), this_period (E), materials_stored (F),
  total_completed_stored (G), pct_complete, balance_to_finish (H), retainage (I)
- `G702Summary`: lines 1–9 (original_contract_sum … balance_to_finish_incl_retainage)

**Outputs**
- `Finding`: rule_id (`R01`…`R10`), rule_name, severity (`error|warning|info`), item_no | None,
  column (`C`…`I`, `pct`, or G702 line `L1`…`L9`), message, expected | None, actual | None,
  dollar_impact (Decimal)
- `CertifiableLine` / `CertifiableResult`: per-line certifiable C/D/E/F/G/I, a rebuilt G702,
  claimed G702, and a variance per line
- `ReviewResult`: context (project, sub, app), certifiable result, findings → serialized to `findings.json`

Every money field gets the same validator: strip `$` and `,`, convert to `Decimal`, and reject
floats, NaN, and blanks. A bad row raises `LoadError("submitted/mech_app_3.csv row 7: this_period: …")`.

---

## 4. Rules

| ID | Name | Severity | Dollar impact |
|----|------|----------|---------------|
| R01 | Row math (G = D+E+F, H = C−G, pct = G/C) | error (pct alone → info) | claimed − recomputed |
| R02 | Scheduled value ≠ SOV + approved COs on that line | error | claimed C − trusted C |
| R03 | From-previous ≠ prior certified D+E (≠ 0 on app 1) | error | claimed D − certified |
| R04 | Over-billing: G > trusted C | error | G − C |
| R05 | Billing on a pending, rejected, or unknown CO line | error | G on that line |
| R06 | Retainage rate wrong (incl. missed or early reduction) | error | claimed I − expected I |
| R07 | Stored materials (F > 0) need backup: invoice, bill of lading, insurance certificate | warning | F |
| R08 | G702 doesn't tie (each line 1–9, localized per D9) | error | claimed − expected |
| R09 | Front-loading heuristic (D6) | warning | 0 (judgment call, reported but not quantified) |
| R10 | Duplicate item numbers, missing SOV lines, extra non-CO lines | error | duplicated or extra G |

R06 emits a single app-level finding when every line consistently uses the wrong rate (e.g. "held
10%, but 5% applies at 52.3% complete"). Otherwise it emits a finding for each line that's off.

---

## 5. Synthetic project and answer key

**Desert Ridge Medical Office Building**, built by a fictional GC, *Arroyo Peak Builders*. Every
name and number is invented. Five monthly apps (Jan–May 2026) for each sub. The generator is
deterministic (no randomness), and a test checks that it reproduces the committed files exactly.

| Sub | Contract | Retainage |
|-----|----------|-----------|
| `mech` Sonoran Air Mechanical | ~$2.45M, ~12 SOV lines | 10% → 5% at 50% |
| `elec` Copperline Electric | ~$1.98M | 10% → 5% at 50% |
| `dryw` Mesa Wall Systems | ~$1.12M | 10% flat |

| App | mech | elec | dryw |
|-----|------|------|------|
| 1 | **clean** | **clean** (PM field cut on branch rough-in, certified lower: a field adjustment, not a finding) | **clean** |
| 2 | R01 math error: G ≠ D+E+F on ductwork | R03 wrong from-previous: carried its own app-1 figure, not the certified one | R09 front-loaded general conditions (PM cuts it back) |
| 3 | R07 stored materials: RTUs delivered, warning only | R06 missed retainage reduction: crossed 50%, still held 10% (negative variance) | R08 G702 line 4 transposed, doesn't tie to G703 |
| 4 | R05 billing on pending CO-03 + R08 G702 line 2 includes the pending CO (stored RTUs correctly moved into E) | **clean** (reduction applied correctly) | **clean** |
| 5 | R04 over-billed line (G > C) | R05 billing on a CO not in the log | R10 duplicate line number |

`expected_findings.json` lists, for each app, the planted errors and the `(rule_id, item_no/line)`
findings each one should produce, plus any PM field adjustments.

`test_answer_key.py` asserts three things:
1. Error findings match the answer key **exactly** for all 15 apps, so there are no false positives.
2. Every planted warning is found.
3. Apps marked clean have **zero findings of any severity**.

A second integration test checks that, for apps 1–4, certifiable(submitted) − certified = the
documented field adjustments (normally $0).

---

## 6. Outputs

- **CLI:** `payapp-reconcile <project_dir> --sub mech --app 4 [--memo] [--out out/] [--front-load-threshold 25]`
  prints a header, a G702 claimed / certifiable / variance table, and a findings table (rich).
  Exit codes: 0 = review ran, 2 = bad input.
- **`out/<sub>_app_<n>/`**: `findings.json`, `review.xlsx`, `memo.md`
- **Excel:**
  - *Summary*: G702 side by side with variance, plus finding counts.
  - *Line Review*: the submitted G703 plus certifiable G and I columns. Problem cells get a red,
    amber, or blue fill and a cell comment.
  - *Findings*: the full findings table.

  All three sheets have frozen headers, currency formats, and set column widths.
- **Memo:** the LLM drafts from `findings.json`. The model comes from `ANTHROPIC_MODEL` (default
  `claude-sonnet-5-5`). There's a **numeric guardrail**: every dollar figure in the LLM's memo must
  appear in the findings or the certifiable summary. If one doesn't, the tool falls back to the
  template and logs a warning. The template path runs when there's no `ANTHROPIC_API_KEY` or
  `LLM_MODE=mock` is set. The client is injected, so tests use a fake.

---

## 7. Milestones (tests run and a commit at the end of each)

| # | Milestone | Commit |
|---|-----------|--------|
| M0 | Scaffold: pyproject, ruff, .gitignore, LICENSE, .env.example, CI, PLAN.md | `chore: scaffold project` |
| M1 | money + models + loaders + tests | `feat: input models and loaders` |
| M2 | contract + rules + sample generator + answer key + tests | `feat: ten review rules with synthetic answer key` |
| M3 | certifiable calculation + integration test | `feat: certifiable amount from trusted inputs` |
| M4 | Excel workbook + golden test | `feat: excel review workbook` |
| M5 | memo (template + LLM + guardrail) | `feat: memo drafting with template fallback` |
| M6 | CLI polish + CLI tests | `feat: rich CLI` |
| M7 | README (real demo output), DESIGN.md, LEARNING.md | `docs: readme, design, learning guide` |

**Finish:** run the full suite with coverage (rules.py ≥ 90%), run the demo, paste the real output
into the README, and write 2 resume bullets using only measured numbers.

**Environment note:** the local machine has Python 3.13. The code avoids anything newer than 3.11,
and CI covers 3.11 and 3.12.
