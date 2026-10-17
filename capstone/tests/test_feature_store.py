"""Tests for the Feature Store.

The store is the training/serving-skew control: training and inference both
ask for features by name and both receive the same definition.  The tests
therefore focus on three properties -- determinism, catalogue integrity, and
loud failure when an extractor misbehaves.
"""

from __future__ import annotations

import math

import pytest

from capstone.domain import Transaction
from capstone.feature_store import (
    FeatureContext,
    FeatureDefinition,
    FeatureNotFoundError,
    FeatureStore,
    build_default_feature_store,
)
from capstone.validation import FeatureStoreError, ValidationError


def _zero_extractor(transaction: Transaction, context: FeatureContext) -> float:
    """A placeholder extractor for construction-time validation tests."""
    return 0.0


ALL_FEATURES = (
    "amount_zscore",
    "channel_risk",
    "is_elevated_category",
    "is_foreign",
    "is_odd_hour",
    "log_amount",
    "velocity",
)


class TestRegistrationAndCatalogue:
    def test_default_store_exposes_the_standard_feature_set(self) -> None:
        assert build_default_feature_store().names() == ALL_FEATURES

    def test_re_registering_the_same_version_is_idempotent(self) -> None:
        """Application start-up must be safe to repeat."""
        store = build_default_feature_store()
        before = store.get("velocity")
        definition = FeatureDefinition(
            name="velocity",
            version=before.version,
            description="re-registered with identical version",
            extractor=lambda txn, ctx: 0.0,
        )
        assert store.register(definition) is before
        assert store.get("velocity") is before

    def test_conflicting_version_under_existing_name_is_refused(self) -> None:
        """Historical scores referenced the old definition; silently
        replacing it would make past decisions unreproducible."""
        store = build_default_feature_store()
        with pytest.raises(FeatureStoreError):
            store.register(
                FeatureDefinition(
                    name="velocity",
                    version="9.9.9",
                    description="changed meaning",
                    extractor=lambda txn, ctx: 99.0,
                )
            )

    def test_unknown_feature_raises_a_specific_error(self) -> None:
        with pytest.raises(FeatureNotFoundError):
            build_default_feature_store().get("no_such_feature")

    def test_describe_returns_catalogue_metadata(self) -> None:
        rows = build_default_feature_store().describe()
        assert [row["name"] for row in rows] == list(ALL_FEATURES)
        assert all(row["version"] and row["owner"] and row["description"] for row in rows)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": "", "version": "1.0.0", "description": "d"},
            {"name": "f", "version": "", "description": "d"},
            {"name": "f", "version": "1.0.0", "description": "d", "extractor": None},
        ],
    )
    def test_definition_validation(self, kwargs: dict) -> None:
        """An unusable definition must be refused at construction, not at
        the first inference call in production."""
        complete = {"extractor": _zero_extractor, **kwargs}
        with pytest.raises(ValidationError):
            FeatureDefinition(**complete)  # type: ignore[arg-type]


class TestFeatureComputation:
    def test_high_risk_vector_matches_hand_calculated_values(
        self, high_risk_transaction: Transaction, context: FeatureContext
    ) -> None:
        vector = build_default_feature_store().compute_vector(
            ALL_FEATURES, high_risk_transaction, context
        )
        assert vector == {
            "log_amount": round(math.log1p(1250.00), 6),
            "amount_zscore": round((1250.00 - 100.0) / 50.0, 6),
            "is_foreign": 1.0,  # GB home country, RU transaction
            "is_elevated_category": 1.0,  # crypto_exchange
            "is_odd_hour": 1.0,  # 02:30 UTC
            "channel_risk": 0.80,  # wire
            "velocity": round(14 / 1, 4),
        }

    def test_low_risk_vector_matches_hand_calculated_values(
        self,
        low_risk_transaction: Transaction,
        quiet_context: FeatureContext,
    ) -> None:
        vector = build_default_feature_store().compute_vector(
            ALL_FEATURES, low_risk_transaction, quiet_context
        )
        assert vector == {
            "log_amount": round(math.log1p(8.50), 6),
            "amount_zscore": round((8.50 - 100.0) / 50.0, 6),
            "is_foreign": 0.0,
            "is_elevated_category": 0.0,
            "is_odd_hour": 0.0,  # 14:30 UTC
            "channel_risk": 0.05,  # branch
            "velocity": round(1 / 4, 4),
        }

    def test_computation_is_deterministic(
        self, high_risk_transaction: Transaction, context: FeatureContext
    ) -> None:
        store = build_default_feature_store()
        first = store.compute_vector(ALL_FEATURES, high_risk_transaction, context)
        second = store.compute_vector(ALL_FEATURES, high_risk_transaction, context)
        assert first == second

    def test_a_weight_mapping_can_be_passed_directly(self, make_transaction) -> None:
        """A model's weight vector can be used as the feature request, which
        is precisely how training/serving skew is prevented."""
        store = build_default_feature_store()
        weights = {"is_foreign": 0.9, "channel_risk": 0.4}
        vector = store.compute_vector(weights, make_transaction(), FeatureContext())
        assert set(vector) == {"is_foreign", "channel_risk"}

    def test_subset_request_computes_only_what_was_asked_for(
        self, high_risk_transaction: Transaction, context: FeatureContext
    ) -> None:
        vector = build_default_feature_store().compute_vector(
            ["is_foreign", "velocity"], high_risk_transaction, context
        )
        assert vector == {"is_foreign": 1.0, "velocity": 14.0}

    def test_empty_request_is_refused(self, high_risk_transaction: Transaction) -> None:
        with pytest.raises(ValidationError):
            build_default_feature_store().compute_vector(
                [], high_risk_transaction, FeatureContext()
            )

    def test_unknown_name_in_a_vector_request_fails_loudly(
        self, high_risk_transaction: Transaction
    ) -> None:
        with pytest.raises(FeatureNotFoundError):
            build_default_feature_store().compute_vector(
                ["is_foreign", "not_a_feature"], high_risk_transaction, FeatureContext()
            )

    def test_zero_standard_deviation_is_guarded(self, make_transaction) -> None:
        """A zero denominator must not abort a whole batch."""
        vector = build_default_feature_store().compute_vector(
            ["amount_zscore"],
            make_transaction(amount=100.0),
            FeatureContext(account_mean_amount=0.0, account_stddev_amount=0.0),
        )
        assert vector["amount_zscore"] == 100.0


class TestMisbehavingExtractors:
    def _store_with(self, extractor) -> FeatureStore:  # type: ignore[no-untyped-def]
        store = FeatureStore()
        store.register(
            FeatureDefinition(
                name="broken", version="1.0.0", description="bad extractor",
                extractor=extractor,
            )
        )
        return store

    def test_non_finite_result_becomes_a_feature_store_error(
        self, make_transaction
    ) -> None:
        store = self._store_with(lambda txn, ctx: float("inf"))
        with pytest.raises(FeatureStoreError):
            store.compute("broken", make_transaction(), FeatureContext())

    def test_nan_is_refused_rather_than_silently_zeroed(self, make_transaction) -> None:
        store = self._store_with(lambda txn, ctx: float("nan"))
        with pytest.raises(FeatureStoreError):
            store.compute("broken", make_transaction(), FeatureContext())

    @pytest.mark.parametrize("value", [True, "1.0", None])
    def test_non_numeric_result_is_refused(self, value: object, make_transaction) -> None:
        store = self._store_with(lambda txn, ctx: value)
        with pytest.raises(FeatureStoreError):
            store.compute("broken", make_transaction(), FeatureContext())

    def test_zero_division_inside_an_extractor_is_not_swallowed(
        self, make_transaction
    ) -> None:
        """The store converts non-numeric results; it does not catch every
        exception, and that distinction is deliberate -- an arithmetic error
        is a bug that must surface, not a value that must be guessed."""
        store = self._store_with(lambda txn, ctx: 1.0 / 0)
        with pytest.raises(ZeroDivisionError):
            store.compute("broken", make_transaction(), FeatureContext())
