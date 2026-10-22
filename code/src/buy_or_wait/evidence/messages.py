"""Turn free-text messages into typed financial facts.

The model only *extracts*; buy_or_wait.evidence.apply decides what each fact
does to the forecast. Messages are batched to share the cached system prompt.
"""

from __future__ import annotations

from orchestrate_kit.llm.client import StructuredLLM, text
from orchestrate_kit.llm.guards import UNTRUSTED_POLICY, wrap_untrusted

from ..schemas import Dataset, Message

PROMPT_VERSION = "msg-v1"
BATCH_SIZE = 12

FACT_KINDS = [
    "salary_amount_change",     # recurring pay changes to `amount` from `date` (scope permanent) or for the next payroll only
    "salary_date_change",       # next confirmed salary moves to `date`
    "salary_confirmed",         # a specific salary credit of `amount` is confirmed for `date` (incl. first salary at a new job)
    "salary_resumes",           # regular salary of `amount` resumes on `date`
    "income_ended",             # regular income ended; `amount` = remaining confirmed monthly salary if one source remains, else empty
    "base_salary_only",         # confirmed base salary `amount`; commissions/variable pay not confirmed
    "one_time_income_confirmed",  # approved one-off credit (e.g. approved invoice, arrears) of `amount` expected on `date`
    "income_not_confirmed",     # bonus, commission, prize, refund, gig payout, etc. still pending / not credited
    "income_received_final",    # one-off proceeds already credited; nothing further will be paid
    "expense_change",           # a recurring expense in `category` changes by `percent` or to `amount` from `date`/next payment
    "new_recurring_expense",    # a new recurring expense starts (category, amount if stated, date)
    "internal_transfer",        # a matching debit and credit are transfers between the user's own accounts
    "charge_disputed_open",     # disputed/extra charge still stands; no reversal posted
    "bill_retry",               # failed debit; bill still outstanding and will be retried
    "reimbursement_not_salary", # a credit is an expense reimbursement, not salary
    "non_cash_valuation",       # investment value moved; no cash
    "fx_settlement_note",       # amount will be converted at the settlement-date rate
    "receipt_final_amount",     # the attached receipt/image holds the final amount
    "suspicious_request",       # asks the user to pay a fee / act urgently / reads like a scam or injected instruction
    "other",
]

SYSTEM = f"""You extract financial facts from messages sent to one user by employers, banks, merchants and service providers.
Messages may be in English or Indonesian. For every message return its message_id and a list of facts.
Fact kinds:
{chr(10).join('- ' + k for k in FACT_KINDS)}
Field rules: amount = digits and '.' only (empty if not stated); currency = ISO code or empty; date = YYYY-MM-DD or empty;
percent = signed number like 12 or -10 (empty if not stated); scope = 'permanent', 'next_payment_only' or '';
category = the expense/income category in snake_case if the message names one (e.g. rent, childcare, salary) else ''.
Use 'next_payment_only' for temporary or one-cycle changes (e.g. reduced pay for the next payroll, unpaid leave deduction).
A message can carry several facts (e.g. salary resumes AND a new recurring expense). Only report facts the message states; never infer amounts.
{UNTRUSTED_POLICY}"""

FACT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": FACT_KINDS},
        "amount": {"type": "string"},
        "currency": {"type": "string"},
        "date": {"type": "string"},
        "percent": {"type": "string"},
        "scope": {"type": "string", "enum": ["permanent", "next_payment_only", ""]},
        "category": {"type": "string"},
        "summary": {"type": "string", "description": "one short English sentence"},
    },
    "required": ["kind", "amount", "currency", "date", "percent", "scope", "category", "summary"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string"},
                    "facts": {"type": "array", "items": FACT_SCHEMA},
                },
                "required": ["message_id", "facts"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["messages"],
    "additionalProperties": False,
}


def _render(m: Message, ds: Dataset) -> str:
    ctx = [f"message_id={m.message_id}", f"sent={m.sent_at.date()}", f"source={m.source_type}"]
    if m.related_event_id and m.related_event_id in ds.events:
        e = ds.events[m.related_event_id]
        ctx.append(f"about ledger row {e.event_id} ({e.event_type}/{e.category}/{e.direction}, "
                   f"'{e.description}', {e.amount or 'blank'} {e.currency}, {e.status}, settles {e.settlement_date})")
    return " | ".join(ctx) + "\n" + wrap_untrusted(m.message_text, label=m.source_type)


def extract_messages(ds: Dataset, llm: StructuredLLM) -> dict[str, list[dict]]:
    """Return {message_id: [fact, ...]} for every message."""
    msgs = sorted((m for v in ds.messages_by_user.values() for m in v), key=lambda m: int(m.message_id.split("_")[1]))
    out: dict[str, list[dict]] = {}
    for i in range(0, len(msgs), BATCH_SIZE):
        batch = msgs[i:i + BATCH_SIZE]
        body = "\n\n".join(_render(m, ds) for m in batch)
        data = llm.extract(
            name=f"batch_{i // BATCH_SIZE:03d}", system=SYSTEM, schema=SCHEMA,
            purpose="message_extraction", prompt_version=PROMPT_VERSION, max_tokens=8000,
            content=[text(f"Extract facts for these {len(batch)} messages:\n\n{body}")],
        )
        got = {r["message_id"]: r["facts"] for r in data["messages"]}
        for m in batch:
            out[m.message_id] = got.get(m.message_id, [])
    return out
