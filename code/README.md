# Buy or Wait? — financial decision agent

For each purchase or payment request, this agent works out how much the user can safely pay today and
whether to pay in full, pay part now, use a supplied installment offer, wait, or not proceed. Every
recommendation keeps the user's balance above their chosen minimum for 90 days.

## Run

```bash
cd code
python3 -m venv .venv && .venv/bin/pip install -e ".[llm,dev]"
export ANTHROPIC_API_KEY=...            # only needed if code/cache/ is missing or inputs changed

.venv/bin/python main.py                # -> ../output.csv and evaluation/usage_report.md
.venv/bin/python main.py --samples      # score against dataset/sample_requests.csv
.venv/bin/python main.py --only request_03 --trace   # full decision trace for one request
.venv/bin/python main.py --no-llm       # never call the API; use cached extractions only
.venv/bin/python -m pytest -q           # 66 tests: kit, business rules per area, adversarial, sample regression
.venv/bin/python package.py             # -> ../code.zip
```

The core engine uses only the Python standard library. `anthropic` is needed only to refresh the
evidence caches. A full run takes about 4 seconds.

## Design

```
dataset/*.csv ──► schemas (typed rows)
images ──► LLM vision extraction ─┐   (cached by content hash, prompt-versioned)
messages ─► LLM fact extraction ──┤
                                  ▼
            evidence.apply  (deterministic rules: what each fact does)
                                  ▼
 ledger ─► recurrence ─► forecast (90-day daily balances) ─► planner ─► explain ─► rules ─► output.csv
```

The LLM only *reads*: it turns payslips, bills and free-text messages (English and Indonesian)
into typed facts. Python decides what each fact does to the money, so instructions hidden in a
message cannot change a decision. Examples of such instructions: "pay the release fee today", or
a forged amount. Each request also gets a structured trace (`--trace`, `--trace-dir`) listing the
evidence used, the series projected, the lowest balance, and every plan rejected and why.

| Package | Purpose |
|---|---|
| `src/orchestrate_kit/` | **Reusable, challenge-agnostic**: typed CSV loading, exact-contract output writer, rule-based validator, per-field scorer, decision traces, provider-agnostic structured LLM client with content-hash cache, usage/cost ledger and report, untrusted-text guards, packaging. |
| `src/buy_or_wait/` | Challenge logic: `ledger` (status, linked-event and duplicate rules, FX), `recurrence` (series detection and projection), `forecast` (safety math), `evidence/` (extraction and application), `spending`, `planner`, `explain`, `rules`. |

### Key financial rules
- **Cash state.** Failed and cancelled rows are dropped. Unrealized valuations are never cash.
  Pending debits are reserved on their settlement date. Pending credits, refunds, bonuses, prizes and
  commissions are not counted. A scheduled "next confirmed salary" is counted and seeds the monthly
  pay cycle.
- **Recurrence.** A category can hold several streams (base pay on the 15th plus commission on the 24th;
  a monthly salary plus weekly platform payouts). Each stream is a monthly day-of-month cluster or a
  fixed cadence (every 5, 7, 10, 14 or 21 days). A clean fixed cadence beats spurious monthly clusters,
  and a cadence only links payments of a similar size.
  One-off spikes above 2.5× the median are excluded. An income stream that misses an expected payment,
  or whose last payroll is marked "final", has ended.
- **Amount estimates.** Repeating income uses its regular amount; variable income uses its recent
  minimum. Variable spending uses the 25th percentile of the last 6 occurrences. That is the value
  that best reproduced the 25 solved samples' decisions (see Calibration).
- **Committed vs projected.** A pending or scheduled row replaces a projected occurrence only when it
  is the same payment (amount within 10%). A different charge in the same category, such as a pending
  fuel authorization or a scheduled insurance top-up, is extra money going out.
- **Safety.** Within a day, debits land before credits. A payment lands after that day's flows and
  reduces every later balance. A plan is safe only if no daily low point in the 90 days falls below
  the minimum.
- **Evidence.** Raises, payday moves, temporary pay, first or resumed salary, ended income, base-only
  pay, rent changes, internal transfers, disputes and duplicate payslips are applied deterministically.
  An approved invoice is counted on its date, and the rest of that user's variable freelance income is
  not projected.
  On conflict, settled history beats a stated estimate.
- **Plan choice.** Plans are ranked exactly as the spec says: completes by the deadline, no spending
  changes, lowest total, earliest start, fewest payments, lowest option id. Spending changes are tried
  only when nothing else is safe. They are limited to permitted, non-protected flexible series, at most 3.

### Calibration
`main.py --samples` scores every output column. Sweeps over estimators, same-day ordering and window
boundaries were run against the samples, and the chosen settings reach 22/25 on status, 23/25 on
method, 22/25 on plan and 22/25 on earliest date. `amount_safe_to_pay` matches within 1% on only a
few samples. The generator seems to work from base amounts rather than the noisy history, so exact
reproduction isn't possible; the estimator was chosen to favour correct decisions.

### Known limitations
- The generator works from whole-number base amounts rather than the noisy history. For example, one
  sample's implied reserve is exactly 487.00 while every history amount has cents. So safe amounts are
  close but rarely exact.
- A failed debit dated just before a regular bill is ignored as the spec says, and the bill is still
  projected (the financially safer reading). Gold may differ here.
- "A new recurring childcare payment begins" messages give no amount and none appears in the data,
  so nothing is reserved for them.

## Cost
Each run's token use and estimated cost are written to `evaluation/usage_report.md`. About 34 model
calls cover the whole dataset. Results are cached, so reruns cost nothing.
