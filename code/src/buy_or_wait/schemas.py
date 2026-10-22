"""Typed rows for every dataset file, plus a one-call loader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from orchestrate_kit.io.csv_loader import PipeList, group_by, index_by, load_rows

OUTPUT_COLUMNS = (
    "request_id", "amount_safe_to_pay", "affordability_status", "recommended_payment_method",
    "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed", "decision_explanation",
)


@dataclass(frozen=True)
class Profile:
    user_id: str
    home_currency: str
    current_available_balance: Decimal
    minimum_balance_to_keep: Decimal
    financial_priorities: PipeList
    expense_categories_to_protect: PipeList
    expense_categories_user_is_willing_to_reduce: PipeList
    expense_categories_user_is_willing_to_stop: PipeList
    payment_methods_user_will_consider: PipeList
    max_installment_months: int | None


@dataclass(frozen=True)
class Event:
    event_id: str
    user_id: str
    event_type: str
    description: str
    category: str
    direction: str
    amount: Decimal | None
    currency: str
    event_date: date
    settlement_date: date | None
    status: str
    linked_event_id: str | None
    flexibility: str
    minimum_allowed_amount: Decimal | None

    @property
    def num(self) -> int:
        return int(self.event_id.rsplit("_", 1)[1])


@dataclass(frozen=True)
class Rate:
    rate_date: date
    from_currency: str
    to_currency: str
    rate: Decimal


@dataclass(frozen=True)
class Request:
    request_id: str
    user_id: str
    request_date: date
    request_type: str
    requested_amount: Decimal
    desired_completion_date: date
    allows_partial_payment: bool
    request_text: str


@dataclass(frozen=True)
class SampleRequest(Request):
    amount_safe_to_pay: str
    affordability_status: str
    recommended_payment_method: str
    payment_plan: str
    earliest_date_for_full_payment: str
    spending_changes_needed: str
    decision_explanation: str


@dataclass(frozen=True)
class PaymentOption:
    payment_option_id: str
    request_id: str
    payment_method: str
    payment_amount: Decimal
    number_of_payments: int
    first_payment_date: date
    payment_frequency_days: int | None
    financing_fee: Decimal
    total_payable_amount: Decimal

    @property
    def num(self) -> int:
        return int(self.payment_option_id.rsplit("_", 1)[1])


@dataclass(frozen=True)
class Message:
    message_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None
    sent_at: datetime
    source_type: str
    message_text: str


@dataclass(frozen=True)
class ImageRef:
    image_id: str
    user_id: str
    request_id: str | None
    related_event_id: str | None


@dataclass
class Dataset:
    root: Path
    profiles: dict[str, Profile]
    events_by_user: dict[str, list[Event]]
    events: dict[str, Event]
    rates: list[Rate]
    requests: list[Request]
    samples: list[SampleRequest]
    options_by_request: dict[str, list[PaymentOption]]
    messages_by_user: dict[str, list[Message]]
    images: list[ImageRef]

    def image_path(self, image_id: str) -> Path:
        return self.root / "media" / "images" / f"{image_id}.png"


def load_dataset(root: str | Path) -> Dataset:
    root = Path(root)
    events = load_rows(root / "financial_events.csv", Event)
    return Dataset(
        root=root,
        profiles=index_by(load_rows(root / "financial_profiles.csv", Profile), "user_id"),
        events_by_user=group_by(events, "user_id"),
        events=index_by(events, "event_id"),
        rates=load_rows(root / "exchange_rates.csv", Rate),
        requests=load_rows(root / "requests.csv", Request),
        samples=load_rows(root / "sample_requests.csv", SampleRequest),
        options_by_request=group_by(load_rows(root / "request_payment_options.csv", PaymentOption), "request_id"),
        messages_by_user=group_by(load_rows(root / "messages.csv", Message), "user_id"),
        images=load_rows(root / "images.csv", ImageRef),
    )
