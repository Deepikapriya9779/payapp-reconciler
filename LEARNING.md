# LEARNING.md: study guide for payapp-reconciler

Use this before interviews. It covers:

1. The construction concepts, explained simply.
2. A guided walkthrough of the code in reading order.
3. Every design decision, with the alternatives considered.
4. 15 likely interview questions with strong answers.
5. Three exercises to do yourself.

The core principle, which you should be able to say in one breath:

> All numbers are computed deterministically in Python using `Decimal`, never floats. The LLM is
> used only to draft the plain-English memo from the structured findings. It never does math or
> makes decisions.

---

## 1. Construction concepts

### Schedule of values (SOV)
At the start of a subcontract, the sub breaks its contract sum into billable lines, such as
"Ductwork $539,000" and "Rooftop units $441,000". The GC approves the breakdown. The lines must
add up to the contract sum. Every monthly bill is "how far along is each line", so the SOV is
the yardstick for the whole job. In the code it's `sov.csv` / `SovLine`, and the loader refuses
an SOV that doesn't total the contract sum.

### G702 and G703 (AIA progress billing)
The **G703 continuation sheet** has one row per SOV line:

| Col | Meaning | Rule of thumb |
|-----|---------|---------------|
| A | Item number | matches the SOV |
| B | Description | |
| C | Scheduled value | SOV + approved change orders |
| D | Work completed from previous applications | **prior D + E**, as certified |
| E | Work completed this period | the new work this month |
| F | Materials presently stored (not in D or E) | delivered, not installed |
| G | Total completed and stored to date | D + E + F |
| % | G ÷ C | over 100% is a red flag |
| H | Balance to finish | C − G |
| I | Retainage | G × retainage rate |

The **G702** is the one-page summary on top:

1. Original contract sum
2. Net change by change orders
3. Contract sum to date (1 + 2)
4. Total completed and stored to date (G703 column G total)
5. Retainage
6. Total earned less retainage (4 − 5)
7. Less previous certificates for payment (line 6 of **last month's certificate**)
8. **Current payment due** (6 − 7)
9. Balance to finish, including retainage (3 − 6)

Line 8 is the check the sub is asking for. Line 7 is the trap: it must come from what the GC
certified last month, not from what the sub asked for last month.

### Certification
The GC reviews the sub's application, adjusts it, and "certifies" an amount. That amount goes
into the GC's own pay application to the owner. The **certified** amount, not the requested
one, is the record. That's why `certified/<sub>_app_<n>.csv` is the source of truth for the next
month's column D.

### PM field verification
The PM walks the job and judges whether ductwork really is 65% complete. The tool can't do
this, so it accepts the sub's claimed progress (E and F). When a PM cuts a line, the cut shows
up in the next month's certificate, and the tool's rule R03 then catches a sub who carries its
own uncut number forward. In the sample, electrical app 1 and drywall app 2 each have a PM cut.

### Retainage (retention)
The GC holds back a percentage of every payment, often 10%, as security that the sub will
finish, fix the punch list and turn in closeout documents. Retainage is released at substantial
or final completion. Many contracts **reduce** retainage, for example from 10% to 5% once the
work is 50% complete. That releases money the GC was holding, and it often needs GC, owner or
surety approval. The tool applies the reduced rate to all work to date once *certifiable*
progress reaches the threshold.

### Change orders (CO)
A change order is a signed change to the contract sum or scope. Statuses:

- **Approved** (executed): it can be billed.
- **Pending** (proposed, still being negotiated): it **cannot** be billed. The GC usually can't
  bill the owner for it either.
- **Rejected**: it will never be billed.

A CO either adjusts an existing SOV line's scheduled value or is carried as its own G703 line
(for example `CO-03`). A sub adding a pending CO to G702 line 2 or billing it on the G703 is one
of the most common errors, and rules R05 and R08 catch it.

### Stored materials
Equipment or material delivered but not yet installed, such as rooftop units sitting on the
roof deck, is billed in column F. It is a risk: it can be stolen or damaged, and the GC must be
sure it is paying for material the sub actually owns. GCs require the supplier invoice, a bill
of lading or delivery ticket, and proof of insurance. Material stored **off site** also needs
title and location documents, and some owners don't allow it at all. Next month, when the units
are set, their value moves from F into E. It does **not** move into D, because D is prior D + E.

### Front-loading
Overvaluing early SOV lines, or billing them ahead of the real work, to get paid faster. It helps
the sub's cash flow. It hurts the GC: if the sub defaults, the unbilled balance may not cover a
replacement sub. Classic example: billing general conditions or supervision at 65% when the
work is 20% done. Some lines are **legitimately** billed early, such as mobilization (usually
capped) and bond and insurance premiums (paid up front). The tool's R09 deliberately ignores
them.

### Over-billing vs. under-billing
Over-billing means billing ahead of work in place, or past the scheduled value. Under-billing
means asking for less than is earned. The tool reports both: a missed retainage reduction is a
negative variance, meaning the GC owes the sub more.

---

## 2. Code walkthrough (reading order)

Read in this order. Each step builds only on the ones before it.

1. **[`money.py`](src/payapp_reconciler/money.py)**: the foundation. `round_half_up` is the
   *only* rounding in the project. `parse_decimal` refuses floats and booleans and accepts
   `"$1,234.56"`. `differs()` is the one-cent tolerance. Notice how small it is.
2. **[`models.py`](src/payapp_reconciler/models.py)**: pydantic models for every input row, the
   G702, findings and results. Things to notice:
   - `Money` is an `Annotated` type, so validation is declared once and reused.
   - `extra="forbid"` rejects unknown CSV columns.
   - `G703Row.work_completed` (D + E) is the concept behind rule R03.
   - `ChangeOrder.approved_as_of(date)` drives every CO decision.
   - `G703_COLUMNS` and `G702_LINES` let findings point at a column letter or a G702 line.
3. **[`loaders.py`](src/payapp_reconciler/loaders.py)**: reads the folder into a `ReviewInput`.
   Look at how a pydantic error becomes `LoadError("file row N: field: message")`, and at the
   checks on trusted data (the SOV must total the contract sum; line and CO numbers must be
   unique).
4. **[`contract.py`](src/payapp_reconciler/contract.py)**: the GC's side of the contract.
   `contract_lines()` turns the SOV plus COs (as of the period end) into the scheduled value the
   GC recognizes for each line, and marks CO-only lines with no approved CO.
   `retainage_rate()` is the reduction rule.
5. **[`certify.py`](src/payapp_reconciler/certify.py)**: rebuilds what can be certified. Trace
   one line by hand:
   - C from the contract
   - D from the prior certificate
   - E and F as claimed, capped so G ≤ C
   - $0 for unapproved CO lines, and duplicates counted once
   - then % complete, then the rate, then I per line

   `certifiable_g702()` turns that into lines 1–9, with line 7 from the certificate.
6. **[`rules.py`](src/payapp_reconciler/rules.py)**: the 10 rules. Start with the registry
   (`RULES`, `@rule`, `apply_rules`), then read R01 (simplest), R03 (the "from previous" check,
   including the stored-materials hint), R06 (one app-level finding when the whole application
   uses the wrong rate) and R08 (localized G702 tie-out).
7. **[`review.py`](src/payapp_reconciler/review.py)**: the orchestrator. It builds the context
   once and returns a `ReviewResult` that becomes `findings.json`.
8. **[`excel.py`](src/payapp_reconciler/excel.py)**: presentation only. It writes values, not
   formulas. Findings are mapped to cells by `(item_no, column)`, then filled by severity and
   commented.
9. **[`memo.py`](src/payapp_reconciler/memo.py)**: the only LLM code. Read `draft_memo` top to
   bottom: mock and no-key checks, then the call, then `unverified_amounts` (the guardrail), with
   the template as the fallback at every step.
10. **[`cli.py`](src/payapp_reconciler/cli.py)**: argparse plus rich, a minimal `.env` loader,
    and exit code 2 on bad input.
11. **[`scripts/generate_sample.py`](scripts/generate_sample.py)**: true progress per line, then a
    `Plan` per app that plants errors, then the answer key. Read `PLANS`: it's the test spec in
    construction language.
12. **Tests**, starting with [`tests/conftest.py`](tests/conftest.py) (the in-memory builders),
    then `test_rules.py`, `test_answer_key.py` and `test_certify.py`.

---

## 3. Design decisions and alternatives

| Decision | Chosen | Alternatives considered | Why |
|----------|--------|-------------------------|-----|
| Money type | `Decimal`, one rounding helper, floats rejected | float; integer cents | Float drifts and needs fudge factors. Integer cents works but makes percent math and display clumsy. `Decimal` reads like the spreadsheet. |
| Rounding | `ROUND_HALF_UP` in `round_half_up` | Python's default (banker's) | Matches Excel and G703 spreadsheets, so tie-outs agree to the cent |
| Who does the math | Python rules | LLM reviews the pay app | Reproducible, explainable, testable. An LLM can't be held to an answer key. |
| LLM role | Memo prose only, with a numeric guardrail and template fallback | No LLM; LLM with no check | The prose genuinely benefits from an LLM. The guardrail means a hallucinated number never reaches the sub. |
| Validation | pydantic v2, `extra="forbid"`, row-numbered errors | dataclasses plus manual checks; pandas | Declarative, strict, good error messages. pandas would add weight and hide row-level errors. |
| Column D | Prior certificate D + E | Prior G; the sub's prior submission | Matches the AIA header. Stored materials move to E when installed. The certificate, not the request, is the record. |
| Certifiable policy | Trust C and D, accept claimed E and F, cap G at C | Trust the claimed G | Trusting G would let an amount the GC cut last month sneak back in through D. If the work is now done, the sub bills it in E. |
| CO status | As of the period end (`approved_date`) | Today's status | Re-reviewing a January app with today's log would wrongly count a CO approved in March |
| CO lines | Both "adjust SOV line" and "own `CO-xx` line" | Own lines only | Both practices exist; supporting them costs a few lines |
| Retainage threshold | Measured on certifiable progress | On claimed progress | Stops a sub over-billing past 50% to get retainage released |
| R06 output | One app-level finding when every line uses the other contract rate, otherwise per line | Always per line | Twelve identical findings for one mistake would bury the real message |
| G702 tie-out | Source lines vs. source, derived lines vs. the sub's own upstream lines | Full recompute vs. every line | One keying error produces one finding, on the line where it happened |
| Front-loading | Keyword list, 25-point threshold, configurable; mobilization and bonds excluded | Tag lines in the SOV; compare to schedule | No schedule input yet. Keywords are simple and explainable. Excluding legitimately early items avoids false positives. |
| Rule registry | `@rule` decorator plus a list | Class hierarchy; plugins | Ten lines, easy to read, and order is deterministic |
| CLI | argparse plus rich | click or typer | No extra dependency for four options |
| Workbook | Values, not formulas | Live formulas | The reviewer sees exactly what the tool computed, and the golden test is stable |
| Sample data | Deterministic generator, committed output, regeneration test | Random data; generated at test time | Reviewers can read the CSVs. The test guarantees data and generator never drift apart. |
| Answer-key test | Exact match, so no extra findings either | "Contains" match | Proves no false positives, which is what a GC actually cares about |

---

## 4. Likely interview questions

**1. What does this do, in one minute?**
A GC gets a pay application from every sub every month. This tool loads the sub's G702 and G703
plus the GC's own records (subcontract, SOV, change order log, last month's certificate). It
runs 10 deterministic checks, rebuilds what the GC can actually certify, and shows claimed vs.
certifiable vs. variance. It writes an Excel review with the problem cells highlighted and
commented, and drafts the correction memo to the sub. On the synthetic project it catches all
12 expected findings across 15 applications with no false positives.

**2. Why `Decimal` instead of float?**
Floats can't represent cents exactly. Summing hundreds of G703 cells drifts, and then you need a
tolerance big enough to hide real one-cent errors. `Decimal` is exact. I round in exactly one
helper with half-up, the way Excel does. I also refuse floats at the input boundary, and JSON is
parsed straight to `Decimal`.

**3. Why not just ask an LLM to review the pay application?**
Because this is a financial control. It has to give the same answer every time, explain every
number, and pass an answer key. An LLM can be right 99 times and transpose a digit the 100th. I
use the LLM where it's good and nothing depends on it, which is writing the memo.

**4. How do you stop the LLM from inventing numbers?**
Three layers. The prompt says to copy figures exactly and never compute. The input is the
structured findings, not raw data. Then a deterministic check pulls every dollar amount out of
the draft and compares it to the amounts the review produced. One unknown figure and the draft
is thrown away for the template. The same fallback covers no key, mock mode, a refusal or an API
error. Tests inject a fake client and can't reach the network.

**5. What's the source of truth for "work completed from previous application"?**
The GC's certificate for the prior application: its D + E on that line. Not the sub's prior
request, because the PM may have cut it. Not prior G, because stored materials aren't completed
work. When they're installed they're billed in E. R03 even recognizes the specific mistake of
moving last month's stored materials into D.

**6. How is the certifiable amount built? What do you trust?**
I trust the GC-controlled inputs: scheduled values from the SOV plus approved COs as of the
period end, D from the prior certificate, and line 7 from the prior certificate's line 6. I
accept the sub's claimed E and F, because field verification is the PM's job. Then I cap G at
C, zero out unapproved CO lines, count duplicates once, and recompute retainage per line at the
contract rate. Variance is claimed minus certifiable on line 8.

**7. How do you handle the retainage reduction?**
Once certifiable progress reaches the threshold (for example 50%), the reduced rate applies to
all work to date. I measure progress on certifiable figures so over-billing can't trigger an
early release. If the whole application uses the other contract rate, I report one app-level
finding with the dollar effect. In the sample, electrical app 3 missed its reduction and the GC
owes $54,720.00 more. The tool is neutral. I'd also say that real contracts vary, and releases
usually need owner or surety sign-off, so the tool flags and the PM decides.

**8. How did you test it?**
Layers:
- At least one passing and one failing unit test per rule (100% coverage on `rules.py`).
- An answer key on 15 synthetic applications with exact matching, so no false positives.
- Clean apps must produce zero findings.
- An integration test: certifiable minus GC-certified must equal the documented PM field cuts.
- A golden-file test on the workbook's key cells, fills and comments.
- Memo tests with a fake client.
- A test that the committed sample data regenerates byte for byte.
- CI runs ruff and pytest on 3.11 and 3.12.

**9. A G702 has a typo on line 4. Doesn't that make lines 6, 8 and 9 wrong too?**
Yes, but if I compared every line to a full recompute I'd report four findings for one typo.
So source lines (1, 2, 4, 5, 7) are compared to their source, and derived lines (3, 6, 8, 9)
are checked against the sub's own upstream lines. The typo is reported once, on line 4, with
its effect on the payment.

**10. A sub bills $220,500 of rooftop units stored on site. What do you do?**
R07 flags it as a warning: get the supplier invoice, a bill of lading or delivery ticket, proof
of insurance, and title and location documents if it's off site. It's certifiable once that
backup is in hand. Next month, when the units are set, the value has to move from F to E, not
into D.

**11. A change order was approved in March. How do you review the February application?**
The CO log shows today's status, so I added an optional `approved_date`. A CO counts only if it
was approved on or before the application's period end. Without that, re-reviewing February
would wrongly expect the March CO in the scheduled value and on G702 line 2.

**12. How would you add PDF pay apps?**
Keep the deterministic core untouched. Add an ingestion step that extracts the G703 table and
G702 lines into the same pydantic models, using a table extractor or an LLM with structured
output. Validation fails loudly on anything that doesn't parse. A person confirms the extracted
totals, and since the G702 and G703 must tie, a bad extraction usually fails R08 by itself. The
rules never know where the data came from.

**13. How would this fit a GC's monthly cycle, with 40 subs?**
Add a batch mode over every `submitted/` file. Have the project accountant work from the Excel
reviews, PMs confirm field progress, and send memos to subs with errors. Certified results
become next month's `certified/` files. Integrations would read exports from the pay-app or
construction-management platform and push certified amounts to accounting.

**14. What are the limitations?**
- No PDF ingestion.
- The data is synthetic.
- It accepts claimed progress, so it can't replace the field walk.
- One retainage rate covers both work and stored materials (no G702 5a/5b).
- One reduction convention.
- Front-loading uses % complete as a stand-in for schedule.
- No lien waiver, insurance or certified-payroll checks.

These are all listed in the README.

**15. Walk me through adding a new rule.**
Write a generator function in `rules.py`, decorated with `@rule("R11", "Name")`. It receives the
context and a `finding` factory and yields findings with a severity, item, column, message and
dollar impact. Add a passing and a failing unit test using the conftest builders, update the
registry test, and optionally plant it in the generator so the answer-key test covers it. Excel
highlighting and the memo pick it up automatically.

---

## 5. Exercises (do these yourself)

### Exercise 1: add rule R11 "Negative this period"
A negative E (backing out previous billing) isn't wrong, but it needs an explanation.
1. Add a warning rule that flags `this_period < 0` on column E. A skeleton is in
   [docs/DESIGN.md §6](docs/DESIGN.md#6-how-to-add-a-new-rule).
2. Add two tests in `tests/test_rules.py`: the clean scenario has no R11 finding, and a row with
   `e="-1500"` produces exactly one warning on column E.
3. Update `test_rule_registry_has_ten_rules_in_order`.
4. Run `pytest` and `ruff check .`. Then run the CLI on any sample app and confirm nothing
   changed (no sample app has a negative E).

*Check yourself:* why does the clean `review()` scenario still pass R03 when E is negative? D is
unchanged, and D is the only column R03 checks.

### Exercise 2: plant a new error in the sample
Plant an **early retainage reduction**: the sub applies 5% before the work reaches 50%.
1. In `scripts/generate_sample.py`, pick mechanical app 3 (certifiable progress 40.64%). Today it
   has a stored-materials plan. Combine them by adding `retainage_rate=D(5)` to that `Plan` and
   an `Expect("R06", "error", None, "I")` entry.
2. Run `python scripts/generate_sample.py` and `pytest`. Everything should pass: the regenerated
   data matches the generator, and the answer-key test now requires R06 on that app (and no
   other new finding).
3. Before running anything else, predict the dollar variance for mechanical app 3 by hand.
   Hint: certifiable is unchanged (10%, because 40.64% is below the 50% threshold), and the sub
   held 5% on $1,020,435.00 completed and stored. Then add your number to `test_sample_variances`
   in `tests/test_certify.py` and confirm it. Is the variance positive or negative, and why?
4. Revert with `git checkout -- scripts data tests` when you're done, or keep it as your own
   commit.

### Exercise 3: separate retainage on stored materials (G702 5a / 5b)
Some subcontracts retain a different percentage on stored materials than on completed work.
1. Add an optional `stored_retainage_pct` to `Subcontract` (blank means the same as work).
2. In `certify.py`, compute I as `apply_pct(D + E, work_rate) + apply_pct(F, stored_rate)`.
3. Update R06 to compare against the same split.
4. Write tests: one line with F > 0 at 10% work and 0% stored.
5. Think about it: what has to change in the G702 model and the R08 tie-out to report lines 5a
   and 5b separately? Write it down as a design note before coding it.
