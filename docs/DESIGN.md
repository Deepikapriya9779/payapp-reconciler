# Design

## 1. Data model

Everything is a pydantic v2 model in [`models.py`](../src/payapp_reconciler/models.py). Money is
`Decimal` rounded to cents. Percents are `Decimal` in percent units (`10` means 10%). Rows are
frozen and reject unknown columns (`extra="forbid"`), so a typo in a CSV header fails loudly
instead of being silently ignored.

### Inputs (one project folder)

| File | Model | Trusted? | Notes |
|------|-------|----------|-------|
| `subcontracts.csv` | `Subcontract` | yes | Retainage %, plus an optional reduced % and threshold. Both must be set or both blank. |
| `sov.csv` | `SovLine` | yes | Lines must total the original contract sum. Line numbers must be unique. |
| `change_orders.csv` | `ChangeOrder` | yes | `status` is approved, pending or rejected. `sov_line_no` is an existing SOV line (adjusts it) or its own `CO-xx` line. Optional `approved_date`. |
| `certified/<sub>_app_<n>.csv` | `G703Row` | yes | What the GC certified. The source of truth for "from previous". |
| `submitted/<sub>_app_<n>.csv` | `G703Row` | no | The sub's G703, columns A–I. |
| `submitted/<sub>_app_<n>_g702.json` | `G702Summary` | no | G702 lines 1–9 as the sub filled them in. |
| `project.json` (optional) | `ProjectInfo` | n/a | Project and GC names for headings. |

The loader ([`loaders.py`](../src/payapp_reconciler/loaders.py)) turns every validation failure
into a `LoadError` that names the file, the spreadsheet row (header = row 1) and the field. For
example: `submitted/mech_app_3.csv row 7: this_period: Value error, not a number: '25k'`. JSON is
parsed with `parse_float=Decimal`, so cents survive even when someone writes numbers rather than
strings.

### Derived and output models

- `ContractLine` (`contract.py`): what the GC recognizes on each line as of the period end.
  That is the scheduled value (SOV plus approved COs), whether it's a CO-only line, and whether
  that line has an approved CO behind it.
- `CertifiableLine` / `CertifiableLines` (`certify.py`): the rebuilt G703, plus % complete and the
  retainage rate that applies.
- `Finding`: `rule_id`, `rule_name`, `severity`, `message`, `item_no` (None for app-level),
  `column` (`A`–`I`, `pct`, or G702 line `L1`–`L9`), `expected`, `actual`, `dollar_impact`.
- `G702Line` / `ReviewResult`: the G702 claimed vs. certifiable and the full result. Serialized
  to `findings.json` with Decimals as strings.

### Sign convention

`dollar_impact` and `variance` are **claimed − correct**, measured on what the sub asked to be
paid. Positive means over-claimed, negative means the GC owes more, and 0 means no direct effect
on the payment (e.g. a wrong balance-to-finish). Finding impacts are gross of retainage. The
variance is on G702 line 8, so it's net of retainage.

## 2. Why Decimal

Floats are binary fractions. `0.1 + 0.2 == 0.30000000000000004`, and adding up a few hundred
G703 cells in floating point drifts by fractions of a cent. A dollar-for-dollar tie-out then
needs fudge factors, and the fudge factor hides real one-cent keying errors. With `Decimal`:

- every amount is exact to the cent;
- rounding happens in **one place**, `money.round_half_up`, with `ROUND_HALF_UP`. That is how
  spreadsheets round, not Python's default banker's rounding (`round(0.025, 2)` → `0.02`);
- floats are refused at the boundary. `parse_decimal(1.1)` raises, because a float has already
  lost the cents.

Comparisons allow exactly one cent of slack (`ReviewConfig.tolerance`), which absorbs
per-line rounding of retainage and nothing more.

## 3. Deterministic checks, LLM only for prose

A pay application review is an accounting control. The output has to be **reproducible** (the
same input always gives the same answer), **explainable** (every finding points to a cell and a
rule), and **testable** (an answer key can say exactly what must be caught). An LLM doing the
math fails all three. It can be right 99 times and transpose a digit the 100th, and nobody can
tell which time that was.

So the split is:

| Job | Who does it | Why |
|-----|-------------|-----|
| Parse and validate inputs | pydantic | Fails loudly, names the row |
| Every number and every finding | Python rules | Exact, reproducible, unit-tested |
| The certifiable amount | Python | Built only from trusted inputs |
| The memo's wording | Claude | Good at tone and clear explanation; nothing depends on it |

The LLM sees `findings.json` (minus line detail) and is told to copy every dollar figure
exactly. Then `memo.unverified_amounts` pulls every `$` amount out of the draft and checks it
against the amounts the review produced. One unknown figure discards the draft and the
deterministic template is used, with the reason recorded. The template is also used when there
is no API key, when `LLM_MODE=mock`, on a refusal, or on any API error. The model comes from
`ANTHROPIC_MODEL` (default `claude-sonnet-5-5`). Server-side refusal fallbacks are requested on
models that support them. The client is injected, so tests use a fake, and an autouse fixture
forces mock mode so no test can reach the network.

## 4. The certifiable amount

[`certify.py`](../src/payapp_reconciler/certify.py) rebuilds the G703 from what the GC
controls, then the G702 from that:

| Column | Certifiable value |
|--------|-------------------|
| C | SOV + change orders approved by the period end |
| D | The GC's prior certificate D + E (0 on application 1) |
| E, F | As claimed, cut (E first, then F) so G never exceeds C |
| G | D + E + F |
| I | G × the subcontract rate, per line |

- Lines for pending, rejected or unlogged change orders certify at $0.
- A duplicated row counts once.
- An SOV line the sub left off still carries its previously certified work, so line 6 never
  drops just because a row went missing.
- G702 line 7 is line 6 of the GC's previous certificate, never the sub's previous request.

**Retainage reduction.** Certifiable % complete is certifiable G ÷ contract sum to date. Once
it reaches the threshold, the reduced rate applies to all work to date. Using certifiable rather
than claimed figures closes the loophole of over-billing past 50% to get retainage released.

**Accepting E as claimed** is deliberate. The tool cannot field-verify progress. When the PM
cuts a line in the field, that cut shows up in the next month's certificate and therefore in the
next month's D check. The integration test `test_certifiable_matches_what_the_gc_certified`
asserts that certifiable − certified equals exactly the PM field cuts listed in the answer key.

## 5. Interpretations worth knowing

| Topic | Choice | Alternative considered |
|-------|--------|------------------------|
| Column D | Prior **D + E** (the AIA header says "From Previous Application (D + E)"). Stored materials move to E when installed. | Prior G. Wrong, because it double-counts stored materials. |
| CO timing | `approved_date` ≤ period end | Today's status. Wrong for historical apps. |
| CO lines | A CO can adjust an SOV line or carry its own `CO-xx` line | Only own lines. Too strict for some GCs. |
| Front-loading | Keywords `general conditions`, `supervision`, `project management`. Flag when > 25 points ahead of the rest of the work. | Including mobilization or bonds. Those are legitimately billed up front, so they'd be false positives. |
| G702 tie-out | Source lines vs. source; derived lines vs. the sub's own upstream lines | Every line vs. a full recompute. One typo would cascade into 4 findings. |
| Missed retainage reduction | Error, negative impact (the GC owes more) | Ignore under-billing. That isn't neutral. |

## 6. How to add a new rule

Rules are generators registered with a decorator in
[`rules.py`](../src/payapp_reconciler/rules.py). Each one gets a `ReviewContext` (the input,
config, trusted contract lines, prior certificate, certifiable lines and de-duplicated rows) and
a `finding` factory with the id and name pre-filled.

1. **Write the rule** at the end of `rules.py`:

   ```python
   @rule("R11", "Negative this period")
   def check_negative_this_period(ctx: ReviewContext, finding: FindingFactory) -> Iterator[Finding]:
       for row in ctx.rows:
           if row.this_period < 0:
               yield finding(
                   severity=Severity.WARNING,
                   item_no=row.item_no,
                   column="E",
                   actual=row.this_period,
                   message=f"{_line(row)}: this period is {fmt_money(row.this_period)}. "
                   "A credit needs a written explanation from the sub.",
               )
   ```

2. **Unit-test it** in `tests/test_rules.py`. Write one test that passes the clean scenario and
   one that catches the problem, using the `row()` / `review()` / `replace_row()` builders in
   `tests/conftest.py`.
3. **Update the registry test** (`test_rule_registry_has_ten_rules_in_order`).
4. **If it should appear in the sample,** plant it in `scripts/generate_sample.py` (add a `Plan`
   with its `Expect` entries), regenerate, and the answer-key test will hold you to it.
5. **Document it** in the README rules table.

There's no Excel or memo change to make. Findings on a G703 column are highlighted and
commented automatically, and G702 findings (`L1`–`L9`) land on the Summary sheet.

## 7. Testing strategy

| Layer | File | What it proves |
|-------|------|----------------|
| Money | `test_money.py` | Half-up rounding, float rejection, formatting |
| Loaders | `test_loaders.py` | Errors name the file, row and field; BOM and `$1,234` input accepted |
| Contract and certifiable | `test_contract.py`, `test_certify.py` | CO timing, retainage threshold, capping, duplicates, missing lines |
| Rules | `test_rules.py` | At least one passing and one failing case per rule; rules module at 100% coverage |
| Answer key | `test_answer_key.py` | All 15 sample apps: findings match the key **exactly** (no false positives); clean apps have zero findings |
| Integration | `test_certify.py` | Certifiable − GC-certified = documented PM field cuts, for every certified app |
| Workbook | `test_excel_golden.py` | Key cells, fills, comments, formats and frozen panes vs. golden JSON (`UPDATE_GOLDEN=1` to refresh) |
| Memo | `test_memo.py` | Template content, fake-client LLM path, guardrail rejection, refusal and error fallback |
| Data | `test_sample_regenerates.py` | `data/sample` is byte-for-byte what the generator produces |
| CLI | `test_cli.py` | Outputs written, exit code 2 on bad input, threshold flag, `.env` precedence |

CI runs ruff (lint and format) and pytest on Python 3.11 and 3.12, with a 90% coverage gate on
`rules.py`.
