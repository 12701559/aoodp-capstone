"""
Feature Store -- centralised, reusable, versioned feature management.

Why this exists
---------------
The naive implementation of "compute the inputs to a risk model" puts the
arithmetic inline in each scoring class.  That produces three problems the
industry calls *training/serving skew*:

1. **Duplication with drift.**  Three scorers each compute
   ``amount / mean_amount``; one of them divides by zero, another rounds
   differently, and the same transaction now scores differently depending on
   which strategy handled it.
2. **No provenance.**  When a decision is challenged there is no record of
   which definition of a feature produced the number.
3. **No reuse.**  A model retrained offline cannot consume the same
   definition that the live engine uses, so the model sees different data
   from the data it was trained on.

The Feature Store resolves all three by making the feature a *registered,
versioned object* with a single owning definition, and by returning the
names and versions of every feature consumed alongside the value.  This is
the concrete meaning of "reusable feature management": one definition, many
consumers, with an auditable version attached to every use.

The store is intentionally read-only after registration.  Mutating a
definition in place would silently change the meaning of every historical
score; a change is a *new version* instead.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping

from .domain import ELEVATED_RISK_CATEGORIES, Transaction
from .validation import FeatureStoreError, ValidationError

# ---------------------------------------------------------------------------
# Context supplied to a feature extractor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeatureContext:
    """Point-in-time reference data a feature extractor may need.

    Kept separate from :class:`Transaction` because it is *background*
    information (the account's own history, current policy) rather than a
    fact about the transaction, and separating them means the same
    transaction can be re-scored against a revised context without
    fabricating a new transaction object.
    """

    home_country: str = "GB"
    account_mean_amount: float = 100.0
    account_stddev_amount: float = 50.0
    recent_txn_count: int = 0
    velocity_window_hours: int = 1
    elevated_categories: frozenset[str] = ELEVATED_RISK_CATEGORIES
    channel_risk: Mapping[str, float] = field(
        default_factory=lambda: {
            "atm": 0.65,
            "branch": 0.05,
            "mobile": 0.35,
            "online": 0.55,
            "pos": 0.20,
            "wire": 0.80,
        }
    )


class FeatureNotFoundError(FeatureStoreError):
    """Raised when a feature name has never been registered."""


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """A named, versioned, owned definition of how to derive one feature."""

    name: str
    version: str
    description: str
    extractor: Callable[[Transaction, FeatureContext], float]
    owner: str = "risk-platform"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValidationError("feature name must be a non-empty string")
        if not isinstance(self.version, str) or not self.version:
            raise ValidationError("feature version must be a non-empty string")
        if not callable(self.extractor):
            raise ValidationError("feature extractor must be callable")

    def compute(self, transaction: Transaction, context: FeatureContext) -> float:
        """Run the extractor and guarantee a finite numeric result.

        The extractor is written by a human and may legitimately divide by a
        zero-valued context field.  Rather than let a ``ZeroDivisionError``
        propagate and abort a whole batch, a non-finite result is converted
        to a ``FeatureStoreError`` -- the failure is loud, attributable and
        cannot silently be treated as ``0.0``.
        """
        value = self.extractor(transaction, context)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FeatureStoreError(
                f"feature {self.name!r} returned {type(value).__name__}, expected a number"
            )
        numeric = float(value)
        if numeric != numeric or numeric in (float("inf"), float("-inf")):
            raise FeatureStoreError(
                f"feature {self.name!r} returned a non-finite value"
            )
        return numeric


class FeatureStore:
    """A thread-safe registry of :class:`FeatureDefinition` objects.

    Thread safety mirrors the discipline established in the Unit 6 banking
    artefact: a single re-entrant lock guards the registry dictionary, and
    every mutation and read takes it.  A re-entrant lock is used rather than
    a plain ``Lock`` because ``compute_vector`` calls ``get`` internally,
    which would self-deadlock with a non-re-entrant lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._definitions: dict[str, FeatureDefinition] = {}

    # -- registration -------------------------------------------------

    def register(self, definition: FeatureDefinition) -> FeatureDefinition:
        """Register a feature definition.

        Re-registering the *same* name and version is a no-op, which makes
        application start-up idempotent.  A differing version under an
        existing name raises, forcing the caller to acknowledge that
        historical scores referenced the previous definition.
        """
        with self._lock:
            existing = self._definitions.get(definition.name)
            if existing is not None:
                if existing.version == definition.version:
                    return existing
                raise FeatureStoreError(
                    f"feature {definition.name!r} already registered at version "
                    f"{existing.version!r}; register {definition.version!r} under a new name"
                )
            self._definitions[definition.name] = definition
            return definition

    # -- lookup ------------------------------------------------------

    def get(self, name: str) -> FeatureDefinition:
        """Return the definition for *name* or raise :class:`FeatureNotFoundError`."""
        with self._lock:
            try:
                return self._definitions[name]
            except KeyError as exc:
                raise FeatureNotFoundError(f"unknown feature {name!r}") from exc

    def names(self) -> tuple[str, ...]:
        """Return all registered feature names in a stable order."""
        with self._lock:
            return tuple(sorted(self._definitions))

    def describe(self) -> list[dict[str, str]]:
        """Return catalogue metadata -- the store's self-describing output."""
        with self._lock:
            return [
                {
                    "name": d.name,
                    "version": d.version,
                    "owner": d.owner,
                    "description": d.description,
                }
                for d in sorted(self._definitions.values(), key=lambda d: d.name)
            ]

    # -- computation -------------------------------------------------

    def compute(
        self, name: str, transaction: Transaction, context: FeatureContext
    ) -> float:
        """Compute one feature for *transaction*."""
        return self.get(name).compute(transaction, context)

    def compute_vector(
        self,
        feature_names: Mapping[str, float] | list[str] | tuple[str, ...],
        transaction: Transaction,
        context: FeatureContext,
    ) -> dict[str, float]:
        """Compute the named features and return them as a ``{name: value}`` map.

        Accepts a weight mapping directly so a caller holding a model's
        weight vector can request exactly the features that model consumes,
        which is how training/serving skew is prevented: both sides ask the
        store for the same names.
        """
        names = list(feature_names)
        if not names:
            raise ValidationError("at least one feature must be requested")
        return {name: self.compute(name, transaction, context) for name in names}


# ---------------------------------------------------------------------------
# Built-in feature library
# ---------------------------------------------------------------------------


def build_default_feature_store() -> FeatureStore:
    """Return a store populated with the capstone's standard feature set.

    Each definition is a small pure function, which is what makes the store
    unit-testable without a transaction database: given a transaction and a
    context, the expected value is computable by hand.
    """
    store = FeatureStore()

    store.register(
        FeatureDefinition(
            name="log_amount",
            version="1.0.0",
            description="Natural log of the transaction amount, dampening large values.",
            extractor=lambda txn, ctx: round(math.log1p(txn.amount), 6),
        )
    )

    store.register(
        FeatureDefinition(
            name="amount_zscore",
            version="1.0.0",
            description="Standard deviations the amount sits from the account's own average.",
            # Guarded denominator: the store converts a non-finite result into
            # a loud FeatureStoreError rather than a silent zero.
            extractor=lambda txn, ctx: round(
                (txn.amount - ctx.account_mean_amount)
                / max(ctx.account_stddev_amount, 1.0),
                6,
            ),
        )
    )

    store.register(
        FeatureDefinition(
            name="is_foreign",
            version="1.0.0",
            description="1.0 when the transaction originates outside the home country.",
            extractor=lambda txn, ctx: 1.0 if txn.country != ctx.home_country else 0.0,
        )
    )

    store.register(
        FeatureDefinition(
            name="is_elevated_category",
            version="1.0.0",
            description="1.0 when the merchant category carries elevated inherent risk.",
            extractor=lambda txn, ctx: (
                1.0 if txn.merchant_category in ctx.elevated_categories else 0.0
            ),
        )
    )

    store.register(
        FeatureDefinition(
            name="is_odd_hour",
            version="1.0.0",
            description="1.0 when the transaction falls in the 01:00-05:59 UTC window.",
            extractor=lambda txn, ctx: 1.0 if 1 <= txn.timestamp.hour < 6 else 0.0,
        )
    )

    store.register(
        FeatureDefinition(
            name="channel_risk",
            version="1.0.0",
            description="Inherent risk weight of the origination channel (0-1).",
            extractor=lambda txn, ctx: float(ctx.channel_risk.get(txn.channel, 0.5)),
        )
    )

    store.register(
        FeatureDefinition(
            name="velocity",
            version="1.0.0",
            description="Transactions per hour on the account within the observation window.",
            extractor=lambda txn, ctx: round(
                ctx.recent_txn_count / max(ctx.velocity_window_hours, 1), 4
            ),
        )
    )

    return store


__all__ = [
    "FeatureContext",
    "FeatureDefinition",
    "FeatureNotFoundError",
    "FeatureStore",
    "build_default_feature_store",
]
