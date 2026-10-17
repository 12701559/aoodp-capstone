"""Tests for the domain model: invariants, immutability and dispatch."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from capstone.domain import (
    RiskDecision,
    RiskScore,
    ScoredBatch,
    ScoredTransaction,
    Transaction,
)
from capstone.reporting import ReportVisitor
from capstone.validation import ValidationError
from capstone.tests.conftest import DAY_TS, FIXED_TS


class TestTransactionInvariants:
    def test_constructs_with_valid_input(self, high_risk_transaction: Transaction) -> None:
        assert high_risk_transaction.amount == 1250.00
        assert high_risk_transaction.channel == "wire"

    def test_transaction_id_defaults_to_a_unique_uuid(self) -> None:
        def build() -> Transaction:
            return Transaction(
                account_number="ACC-1",
                amount=10.0,
                channel="pos",
                merchant_category="groceries",
                country="GB",
            )

        assert build().txn_id != build().txn_id

    @pytest.mark.parametrize(
        "field,value",
        [
            ("channel", "telegraph"),
            ("merchant_category", "unclassified"),
            ("country", "GBR"),
            ("account_number", "ACC 1"),
        ],
    )
    def test_rejects_out_of_vocabulary_values(
        self, make_transaction, field: str, value: str
    ) -> None:
        with pytest.raises(ValidationError):
            make_transaction(**{field: value})

    def test_rejects_naive_timestamp(self, make_transaction) -> None:
        """A naive timestamp makes the odd-hour feature ambiguous."""
        with pytest.raises(ValidationError):
            make_transaction(timestamp=datetime(2026, 3, 4, 2, 30))

    def test_rejects_non_datetime_timestamp(self, make_transaction) -> None:
        with pytest.raises(ValidationError):
            make_transaction(timestamp="2026-03-04T02:30:00Z")

    def test_is_immutable(self, high_risk_transaction: Transaction) -> None:
        with pytest.raises(FrozenInstanceError):
            high_risk_transaction.amount = 1.0  # type: ignore[misc]

    def test_equality_is_value_based(self, make_transaction) -> None:
        assert make_transaction() == make_transaction()
        assert make_transaction(amount=999.0) != make_transaction()


class TestRiskScore:
    @staticmethod
    def _score(**overrides: object) -> RiskScore:
        payload: dict[str, object] = {
            "score": 0.61,
            "decision": RiskDecision.REVIEW,
            "rationale": "two rules fired",
            "strategy_name": "rules-v1",
            "model_version": "transaction-risk:2.1.0",
            "contributing_features": {"is_foreign>0.5": 0.15},
        }
        payload.update(overrides)
        return RiskScore(**payload)  # type: ignore[arg-type]

    def test_accepts_a_well_formed_score(self) -> None:
        assert self._score().decision is RiskDecision.REVIEW

    @pytest.mark.parametrize("score", [-0.01, 1.01, float("nan")])
    def test_rejects_scores_outside_the_unit_interval(self, score: float) -> None:
        with pytest.raises(ValidationError):
            self._score(score=score)

    def test_rejects_non_enum_decision(self) -> None:
        with pytest.raises(ValidationError):
            self._score(decision="review")

    @pytest.mark.parametrize("rationale", ["", "   ", None])
    def test_requires_a_non_empty_rationale(self, rationale: object) -> None:
        with pytest.raises(ValidationError):
            self._score(rationale=rationale)

    def test_as_record_is_serialisable_and_auditable(self) -> None:
        record = self._score(score=0.123456789).as_record()
        assert record["score"] == 0.123457  # rounded, not raw float noise
        assert record["decision"] == "review"
        assert record["contributions"] == {"is_foreign>0.5": 0.15}

    def test_is_immutable(self) -> None:
        with pytest.raises(FrozenInstanceError):
            self._score().score = 0.0  # type: ignore[misc]


class _OrderRecordingVisitor(ReportVisitor):
    """Records visit order to prove the traversal contract."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def visit_batch(self, batch: ScoredBatch) -> None:
        self.events.append("batch-start")

    def visit_batch_end(self, batch: ScoredBatch) -> None:
        self.events.append("batch-end")

    def visit_scored_transaction(self, element: ScoredTransaction) -> None:
        self.events.append(f"txn:{element.transaction.txn_id}")

    def result(self) -> list[str]:
        return self.events


class TestVisitorElements:
    @staticmethod
    def _element(txn_id: str, make_transaction) -> ScoredTransaction:
        return ScoredTransaction(
            transaction=make_transaction(txn_id=txn_id),
            risk_score=RiskScore(
                score=0.9,
                decision=RiskDecision.BLOCK,
                rationale="many rules fired",
                strategy_name="rules-v1",
                model_version="rules:transaction-risk:2.1.0",
            ),
        )

    def test_scored_transaction_dispatches_to_the_visitor(
        self, make_transaction
    ) -> None:
        visitor = _OrderRecordingVisitor()
        self._element("txn-a", make_transaction).accept(visitor)
        assert visitor.events == ["txn:txn-a"]

    def test_batch_visits_children_in_order_between_hooks(
        self, make_transaction
    ) -> None:
        batch = ScoredBatch(
            items=(
                self._element("txn-a", make_transaction),
                self._element("txn-b", make_transaction),
            )
        )
        visitor = _OrderRecordingVisitor()
        batch.accept(visitor)
        assert visitor.events == [
            "batch-start",
            "txn:txn-a",
            "txn:txn-b",
            "batch-end",
        ]

    def test_batch_supports_iteration_and_length(self, make_transaction) -> None:
        batch = ScoredBatch(items=(self._element("txn-a", make_transaction),))
        assert len(batch) == 1
        assert [item.transaction.txn_id for item in batch] == ["txn-a"]

    def test_empty_batch_still_produces_batch_hooks(self) -> None:
        visitor = _OrderRecordingVisitor()
        ScoredBatch().accept(visitor)
        assert visitor.events == ["batch-start", "batch-end"]

    def test_timestamp_ordering_is_preserved_by_construction(
        self, make_transaction
    ) -> None:
        """Domain timestamps are comparable when both are timezone-aware."""
        early = make_transaction(timestamp=FIXED_TS, txn_id="txn-early")
        late = make_transaction(timestamp=DAY_TS, txn_id="txn-late")
        assert early.timestamp < late.timestamp
        assert early.timestamp.utcoffset() == timezone.utc.utcoffset(None)
