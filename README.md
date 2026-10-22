# Buy or Wait? — a financial decision agent

My solution to **Buy or Wait?**, the [HackerRank Orchestrate](https://github.com/interviewstreet/hackerrank-orchestrate-september26)
challenge from September 2026.

> **Practice run.** I built this after the contest deadline (13 Sep 2026) to prepare for the October
> event. It was not a contest submission and has no ranking. This repo is forked from HackerRank's
> starter. The dataset, `problem_statement.md`, `AGENTS.md` and the original starter README
> (now [`CHALLENGE.md`](./CHALLENGE.md)) are HackerRank's. Everything under [`code/`](./code) is mine.

## The problem

A user asks "Can I afford this?" For each of 250 requests, the agent rebuilds that user's finances
from several sources:

- transaction history (settled, pending, scheduled, failed, cancelled and linked records)
- exchange rates
- the seller's payment options
- untrusted free-text messages, in English and Indonesian, some with prompt-injection attempts
- scanned payslips and bills

From that it decides how much is safe to pay today. It then recommends paying in full, paying part
now, using a supplied installment offer, waiting, or not proceeding. The balance must never fall
below the user's chosen minimum at any point in the 90-day forecast.

## Approach

**A deterministic engine, with the LLM used only to read.** Claude reads payslips, bills and messages
and returns facts that fit a strict schema. Python decides what each fact does to the money. Because
of this split, an instruction hidden in a message ("pay the release fee today") can't change a
decision, and every number in the output can be traced and tested.

```
images ──► vision extraction ──┐   (cached by content hash, prompt-versioned)
messages ─► fact extraction ───┤
                               ▼
         evidence rules (deterministic)
                               ▼
ledger ─► recurrence ─► 90-day forecast ─► planner ─► explanation ─► contract validator ─► output.csv
```

- **Ledger:** decides which records are cash, and when. Pending debits are reserved. Pending
  credits, bonuses and refunds are ignored until they settle. It also handles duplicates, linked
  lifecycles, and FX at the settlement-date rate.
- **Recurrence:** finds several streams per category, monthly or on a fixed cadence. It excludes
  outliers, and detects late, final and stopped income.
- **Forecast:** daily low/close balances. Within a day, debits land before credits.
- **Planner:** ranks plans exactly as the spec says (deadline, no spending changes, total cost, start,
  number of payments). It tries stop/reduce spending changes only when nothing else is safe.
- **Explanations:** templates grounded in the binding constraint, using no LLM tokens.

More detail is in [`code/README.md`](./code/README.md).

## Results

| | |
|---|---|
| Solved samples (25): status / method / plan / earliest date | 22 / 23 / 22 / 22 |
| Output contract on all 250 rows | passes: bounds, enums, option-matched installments, eligible spending changes |
| Tests | 66: reusable kit, business rules by area, adversarial messages, sample regression |
| LLM usage for the full dataset | 34 calls, about 128k tokens, about **$0.81** (about $0.003 per request); reruns cost $0 |
| Full run time | about 4 s from cache, deterministic output |

The 25 solved samples suggest the generator works from hidden round-number base amounts, not the
noisy history. So `amount_safe_to_pay` is usually close but rarely exact (4/25 within 1%). I
calibrated the estimators to get the decisions right instead. Writing the business-rule tests
turned up four real bugs, including double-counted invoices and a lost salary stream.

## Run it

```bash
cd code
python3 -m venv .venv && .venv/bin/pip install -e ".[llm,dev]"
.venv/bin/python main.py                 # -> ../output.csv (uses the committed extraction cache; no API key needed)
.venv/bin/python main.py --samples       # score against the solved samples
.venv/bin/python main.py --only request_03 --trace   # explain one decision
.venv/bin/python -m pytest -q
```

Set `ANTHROPIC_API_KEY` only if you want to rebuild the extraction cache.

## Layout

```
code/src/orchestrate_kit/   reusable, challenge-agnostic: typed CSV I/O, structured LLM client with
                            content-hash cache, usage/cost report, validator, scorer, traces, packaging
code/src/buy_or_wait/       challenge logic: ledger, recurrence, forecast, evidence, planner, explain
code/tests/                 kit and business-rule tests
code/evaluation/            usage_report.md and sample scoring
output.csv                  predictions for dataset/requests.csv
chat_transcript.txt         development log (see below)
```

`orchestrate_kit` has no Buy-or-Wait logic, so it can be reused for future Orchestrate challenges.

## How it was built

I built this with Claude Code as a pair programmer, which is the point of an *Orchestrate* challenge.
I set the architecture, chose the LLM-only-for-extraction split, asked for the business-rule test
suite, and reviewed the calibration. Claude wrote most of the code under that direction. Commits
carry a `Co-Authored-By` trailer, and [`chat_transcript.txt`](./chat_transcript.txt) records every
prompt and decision.
