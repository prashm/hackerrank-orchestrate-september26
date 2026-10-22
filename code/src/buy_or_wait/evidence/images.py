"""Extract amounts from evidence images (payslips, bills, receipts, invoices)."""

from __future__ import annotations

from decimal import Decimal

from orchestrate_kit.llm.client import StructuredLLM, image_png, text
from orchestrate_kit.llm.guards import UNTRUSTED_POLICY, wrap_untrusted

from ..schemas import Dataset, Event

PROMPT_VERSION = "img-v1"

SYSTEM = f"""You read financial documents (payslips, utility bills, invoices, receipts, statements) for a personal-finance assistant.
You are told which ledger row the document supports. Return the single amount that row represents, in the document's currency:
- payslip supporting a salary credit: the NET pay actually transferred (not gross, not a subtotal);
- bill / invoice supporting a debit: the total amount payable (including tax), not a subtotal or a single line;
- receipt: the final amount paid.
Report figures exactly as printed (no rounding). If the document shows several currencies, report the one actually charged/credited.
{UNTRUSTED_POLICY}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["payslip", "bill", "invoice", "receipt", "statement", "other"]},
        "currency": {"type": "string", "description": "ISO code of the amount"},
        "amount": {"type": "string", "description": "the amount the ledger row represents, digits and '.' only"},
        "amount_label": {"type": "string", "description": "label printed next to the amount (e.g. 'Net Pay', 'Total due')"},
        "document_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
        "due_date": {"type": "string", "description": "YYYY-MM-DD or empty"},
        "period": {"type": "string", "description": "pay/billing period if printed, else empty"},
        "other_amounts": {
            "type": "array",
            "items": {"type": "object", "properties": {"label": {"type": "string"}, "amount": {"type": "string"}},
                      "required": ["label", "amount"], "additionalProperties": False},
        },
        "suspicious_instructions": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["doc_type", "currency", "amount", "amount_label", "document_date", "due_date", "period",
                 "other_amounts", "suspicious_instructions", "confidence"],
    "additionalProperties": False,
}


def _row_context(e: Event | None) -> str:
    if e is None:
        return "No ledger row is linked; summarise the document's main payable or credited amount."
    return (f"Ledger row {e.event_id}: {e.event_type} / {e.category} / {e.direction}, "
            f"description '{e.description}', currency {e.currency}, event date {e.event_date}, "
            f"status {e.status}. The row's amount is blank and must come from this document.")


def extract_images(ds: Dataset, llm: StructuredLLM) -> dict[str, dict]:
    """Return {image_id: extraction} for every image in images.csv."""
    out: dict[str, dict] = {}
    for ref in ds.images:
        path = ds.image_path(ref.image_id)
        if not path.exists():
            continue  # never invent evidence for a missing file
        ev = ds.events.get(ref.related_event_id) if ref.related_event_id else None
        data = llm.extract(
            name=ref.image_id, system=SYSTEM, purpose="image_extraction", prompt_version=PROMPT_VERSION,
            schema=SCHEMA,
            content=[image_png(path.read_bytes()), text(wrap_untrusted(_row_context(ev), label="ledger-context"))],
        )
        data["image_id"] = ref.image_id
        data["related_event_id"] = ref.related_event_id
        out[ref.image_id] = data
    return out


def amount_of(extraction: dict) -> Decimal | None:
    try:
        return Decimal(extraction["amount"].replace(",", ""))
    except Exception:
        return None
