"""
Domain model for the AI-assisted transaction risk assessment capstone.

These classes are pure data plus validation.  They hold no reference to any
scoring algorithm, model provider or reporting concern, which is what makes
the architecture testable and what allows the Visitor pattern to attach new
behaviour without touching these definitions.

Immutability is deliberate.  A :class:`Transaction` is a fact about the past
and a :class:`RiskScore` is a decision that must remain reproducible and
auditable; allowing either to be edited after the fact would destroy the
audit trail that the ethical analysis in the portfolio depends on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Iterator, Mapping, Sequence

from .validation import (
    ValidationError,
    validate_account_number,
    validate_amount,
    validate_choice,
    validate_country,
    validate_identifier,
    validate_unit_interval,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from .reporting import ReportVisitor


# Closed vocabularies.  Anything outside these sets is rejected at the
# boundary, so no scoring rule can ever receive an unexpected category.
VALID_CHANNELS: frozenset[str] = frozenset(
    {"atm", "branch", "mobile", "online", "pos", "wire"}
)

VALID_MERCHANT_CATEGORIES: frozenset[str] = frozenset(
    {
        "cash",
        "crypto_exchange",
        "electronics",
        "gambling",
        "groceries",
        "money_transfer",
        "travel",
        "utilities",
    }
)

# Categories that carry elevated inherent risk.  Held as data rather than
# hard-coded inside a scoring branch so the policy can be revised without
# touching algorithmic code (Open/Closed Principle).
ELEVATED_RISK_CATEGORIES: frozenset[str] = frozenset(
    {"cash", "crypto_exchange", "gambling", "money_transfer"}
)


class RiskDecision(Enum):
    """The action a risk score implies.

    Modelled as an enum rather than a string so an invalid decision is a
    programming error caught immediately, not a silent string mismatch at
    a downstream gateway.
    """

    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Transaction:
    """An immutable record of a single attempted money movement.

    Validation happens in :meth:`__post_init__`, so it is impossible to
    construct an invalid ``Transaction``: the type system and the constructor
    together guarantee the invariants every downstream component relies on.
    """

    account_number: str
    amount: float
    channel: str
    merchant_category: str
    country: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    txn_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        validate_account_number(self.account_number)
        validate_amount(self.amount)
        validate_choice(self.channel, VALID_CHANNELS, "channel")
        validate_choice(
            self.merchant_category, VALID_MERCHANT_CATEGORIES, "merchant_category"
        )
        validate_country(self.country)
        validate_identifier(self.txn_id, "txn_id")
        if not isinstance(self.timestamp, datetime):
            raise ValidationError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None:
            # A naive timestamp makes "odd hour" features ambiguous, so the
            # ambiguity is refused rather than silently assumed to be UTC.
            raise ValidationError("timestamp must be timezone-aware")


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskScore:
    """The output of one scoring pass: a number, a decision and a reason.

    ``contributing_features`` carries the per-feature contribution that
    produced the score.  Without it the score would be a black-box output
    and the engine could not be audited or explained to the account holder;
    carrying it is the design-level response to the explainability problem
    raised in the module's ethics reading.
    """

    score: float
    decision: RiskDecision
    rationale: str
    strategy_name: str
    model_version: str
    contributing_features: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_unit_interval(self.score)
        if not isinstance(self.decision, RiskDecision):
            raise ValidationError("decision must be a RiskDecision member")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValidationError("rationale must be a non-empty string")

    def as_record(self) -> dict[str, object]:
        """Return a JSON-serialisable dictionary for the audit sink."""
        return {
            "score": round(self.score, 6),
            "decision": self.decision.value,
            "rationale": self.rationale,
            "strategy": self.strategy_name,
            "model_version": self.model_version,
            "contributions": dict(self.contributing_features),
        }


# ---------------------------------------------------------------------------
# Visitor elements
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoredTransaction:
    """A transaction paired with the verdict reached about it (visitor element)."""

    transaction: Transaction
    risk_score: RiskScore

    def accept(self, visitor: "ReportVisitor") -> None:
        """Dispatch to the visitor's ``visit_scored_transaction`` method.

        This is the double-dispatch core of the Visitor pattern.  The
        *element* decides which method runs, based on its concrete type,
        and the *visitor* decides what to do -- so a new report is a new
        visitor class and no element here changes at all.
        """
        visitor.visit_scored_transaction(self)


@dataclass(frozen=True, slots=True)
class ScoredBatch:
    """An ordered collection of scored transactions (composite visitor element)."""

    items: Sequence[ScoredTransaction] = ()

    def __iter__(self) -> Iterator[ScoredTransaction]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def accept(self, visitor: "ReportVisitor") -> None:
        """Visit the batch itself, then each child element in order."""
        visitor.visit_batch(self)
        for item in self.items:
            item.accept(visitor)
        visitor.visit_batch_end(self)
