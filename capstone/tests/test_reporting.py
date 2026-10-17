"""Tests for the reporting subsystem (the Visitor pattern).

What these tests are for
------------------------
A Visitor is easy to write and easy to get subtly wrong: traversal order,
accumulated state, and what the report *omits* are all things that a
plausible-looking implementation can get wrong silently.  The suite therefore
asserts three things that a functional test alone would miss:

1. **Traversal contract** -- ``visit_batch`` precedes the children and
   ``visit_batch_end`` follows them, so accumulating visitors can rely on it.
2. **Dispatching is double** -- the visitor is selected by the element's own
   ``accept`` call, so a new element type cannot be silently skipped.
3. **Reports minimise data** -- the JSON export and the blocked list carry the
   account *suffix only*, which is a privacy property of the report rather
   than of the source record.  That is asserted, not assumed.
"""

from __future__ import annotations

import json

import pytest

from capstone.domain import (
    RiskDecision,
    RiskScore,
    ScoredBatch,
    ScoredTransaction,
    Transaction,
)
from capstone.reporting import (
    BlockedTransactionVisitor,
    ChannelRiskVisitor,
    JsonExportVisitor,
    ReportVisitor,
    RiskSummaryVisitor,
    render,
)
from capstone.validation import ValidationError

from .conftest import DAY_TS, FIXED_TS

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def make_scored(
    txn_id: str,
    *,
    score: float,
    decision: RiskDecision,
    channel: str = "wire",
    amount: float = 100.0,
    account_number: str = "ACC-00001234",
    strategy_name: str = "rules-v1",
    contributions: dict[str, float] | None = None,
) -> ScoredTransaction:
    """Return a scored transaction with a fully specified verdict."""
    txn = Transaction(
        account_number=account_number,
        amount=amount,
        channel=channel,
        merchant_category="crypto_exchange",
        country="RU",
        timestamp=FIXED_TS,
        txn_id=txn_id,
    )
    risk = RiskScore(
        score=score,
        decision=decision,
        rationale=f"score {score} from {strategy_name}",
        strategy_name=strategy_name,
        model_version="transaction-risk:2.1.0",
        contributing_features=contributions or {"channel_risk": 0.9},
    )
    return ScoredTransaction(transaction=txn, risk_score=risk)


@pytest.fixture()
def mixed_batch() -> ScoredBatch:
    """Three transactions spanning all three decisions and two channels."""
    return ScoredBatch(
        items=(
            make_scored("t-allow", score=0.10, decision=RiskDecision.ALLOW, channel="branch"),
            make_scored(
                "t-review", score=0.55, decision=RiskDecision.REVIEW, channel="wire", amount=500.0
            ),
            make_scored(
                "t-block",
                score=0.95,
                decision=RiskDecision.BLOCK,
                channel="wire",
                amount=2500.0,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


class RecordingVisitor(ReportVisitor):
    """A spy that records the exact sequence of visits it receives."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def visit_batch(self, batch: ScoredBatch) -> None:
        self.events.append(f"batch({len(batch)})")

    def visit_scored_transaction(self, element: ScoredTransaction) -> None:
        self.events.append(element.transaction.txn_id)

    def visit_batch_end(self, batch: ScoredBatch) -> None:
        self.events.append("batch-end")

    def result(self) -> list[str]:
        return self.events


class TestTraversal:
    def test_visit_order_is_batch_then_children_then_end(self, mixed_batch: ScoredBatch) -> None:
        assert render(mixed_batch, RecordingVisitor()) == [
            "batch(3)",
            "t-allow",
            "t-review",
            "t-block",
            "batch-end",
        ]

    def test_default_hooks_are_no_ops(self, mixed_batch: ScoredBatch) -> None:
        """A visitor that overrides one method still works (interface segregation)."""

        class Minimal(ReportVisitor):
            def __init__(self) -> None:
                self.count = 0

            def visit_scored_transaction(self, element: ScoredTransaction) -> None:
                self.count += 1

            def result(self) -> int:
                return self.count

        assert render(mixed_batch, Minimal()) == 3

    def test_dispatch_uses_the_element_not_the_caller(self, mixed_batch: ScoredBatch) -> None:
        """Proof of double dispatch: the batch delegates to each element."""
        visitor = RecordingVisitor()
        mixed_batch.accept(visitor)
        assert visitor.events[1:] == ["t-allow", "t-review", "t-block", "batch-end"]

    def test_empty_batch_is_visited_but_yields_nothing(self) -> None:
        assert render(ScoredBatch(items=()), RecordingVisitor()) == ["batch(0)", "batch-end"]

    def test_render_rejects_a_non_visitor(self, mixed_batch: ScoredBatch) -> None:
        with pytest.raises(ValidationError, match="ReportVisitor"):
            render(mixed_batch, object())  # type: ignore[arg-type]

    def test_report_name_defaults_to_the_class_name(self) -> None:
        assert RiskSummaryVisitor().report_name == "RiskSummaryVisitor"
        assert ChannelRiskVisitor().report_name == "ChannelRiskVisitor"


# ---------------------------------------------------------------------------
# RiskSummaryVisitor
# ---------------------------------------------------------------------------


class TestRiskSummaryVisitor:
    def test_aggregates_counts_and_mean(self, mixed_batch: ScoredBatch) -> None:
        result = render(mixed_batch, RiskSummaryVisitor())
        assert result["total_transactions"] == 3
        assert result["counts_by_decision"] == {"allow": 1, "review": 1, "block": 1}
        assert result["mean_score"] == pytest.approx((0.10 + 0.55 + 0.95) / 3, abs=1e-6)

    def test_blocked_value_counts_only_blocked_transactions(
        self, mixed_batch: ScoredBatch
    ) -> None:
        """A review is not a block; conflating them would overstate exposure."""
        assert render(mixed_batch, RiskSummaryVisitor())["blocked_value"] == 2500.0

    def test_highest_risk_is_the_maximum_score(self, mixed_batch: ScoredBatch) -> None:
        highest = render(mixed_batch, RiskSummaryVisitor())["highest_risk"]
        assert highest["txn_id"] == "t-block"
        assert highest["decision"] == "block"
        assert "rationale" in highest

    def test_breaks_down_by_channel_and_strategy(self, mixed_batch: ScoredBatch) -> None:
        result = render(mixed_batch, RiskSummaryVisitor())
        assert result["counts_by_channel"] == {"branch": 1, "wire": 2}
        assert result["counts_by_strategy"] == {"rules-v1": 3}

    def test_empty_batch_has_no_division_by_zero(self) -> None:
        result = render(ScoredBatch(items=()), RiskSummaryVisitor())
        assert result["total_transactions"] == 0
        assert result["mean_score"] == 0.0
        assert result["highest_risk"] is None

    def test_result_is_json_serialisable(self, mixed_batch: ScoredBatch) -> None:
        assert json.loads(json.dumps(render(mixed_batch, RiskSummaryVisitor())))


# ---------------------------------------------------------------------------
# BlockedTransactionVisitor
# ---------------------------------------------------------------------------


class TestBlockedTransactionVisitor:
    def test_extracts_only_blocked_transactions(self, mixed_batch: ScoredBatch) -> None:
        findings = render(mixed_batch, BlockedTransactionVisitor())
        assert [f["txn_id"] for f in findings] == ["t-block"]

    def test_score_threshold_filters_blocks(self, mixed_batch: ScoredBatch) -> None:
        assert render(mixed_batch, BlockedTransactionVisitor(minimum_score=0.99)) == []

    def test_findings_are_ordered_by_descending_score(self) -> None:
        batch = ScoredBatch(
            items=(
                make_scored("low", score=0.81, decision=RiskDecision.BLOCK),
                make_scored("high", score=0.99, decision=RiskDecision.BLOCK),
                make_scored("mid", score=0.90, decision=RiskDecision.BLOCK),
            )
        )
        findings = render(batch, BlockedTransactionVisitor())
        assert [f["txn_id"] for f in findings] == ["high", "mid", "low"]

    def test_reports_only_the_account_suffix(self, mixed_batch: ScoredBatch) -> None:
        """A review queue does not need the full account number."""
        finding = render(mixed_batch, BlockedTransactionVisitor())[0]
        assert finding["account_suffix"] == "1234"
        assert finding["account_suffix"] != "ACC-00001234"
        assert "account_number" not in finding

    def test_amount_is_rounded_for_presentation(self) -> None:
        batch = ScoredBatch(
            items=(make_scored("t", score=0.9, decision=RiskDecision.BLOCK, amount=2500.6789),)
        )
        assert render(batch, BlockedTransactionVisitor())[0]["amount"] == 2500.68

    @pytest.mark.parametrize("threshold", [-0.01, 1.01, 2])
    def test_threshold_must_be_a_probability(self, threshold: float) -> None:
        with pytest.raises(ValidationError, match="minimum_score"):
            BlockedTransactionVisitor(minimum_score=threshold)


# ---------------------------------------------------------------------------
# JsonExportVisitor
# ---------------------------------------------------------------------------


class TestJsonExportVisitor:
    def test_exports_metadata_and_one_record_per_transaction(
        self, mixed_batch: ScoredBatch
    ) -> None:
        document = json.loads(render(mixed_batch, JsonExportVisitor()))
        assert document["metadata"] == {"element": "batch", "size": 3}
        assert len(document["transactions"]) == 3

    def test_record_carries_provenance(self, mixed_batch: ScoredBatch) -> None:
        document = json.loads(render(mixed_batch, JsonExportVisitor()))
        record = document["transactions"][2]
        assert record["txn_id"] == "t-block"
        assert record["decision"] == "block"
        assert record["strategy"] == "rules-v1"
        assert record["model_version"] == "transaction-risk:2.1.0"
        assert record["timestamp"] == FIXED_TS.isoformat()

    def test_export_never_contains_the_full_account_number(
        self, mixed_batch: ScoredBatch
    ) -> None:
        """The export travels further and lives longer than the source data."""
        raw = render(mixed_batch, JsonExportVisitor())
        assert "ACC-00001234" not in raw
        assert json.loads(raw)["transactions"][0]["account_suffix"] == "1234"

    def test_indent_is_honoured(self, mixed_batch: ScoredBatch) -> None:
        raw = render(mixed_batch, JsonExportVisitor(indent=2))
        assert "\n" in raw
        assert json.loads(raw)["metadata"]["size"] == 3

    def test_timestamps_are_serialised_not_stringified_loosely(self) -> None:
        batch = ScoredBatch(
            items=(
                ScoredTransaction(
                    transaction=Transaction(
                        account_number="ACC-00009999",
                        amount=10.0,
                        channel="branch",
                        merchant_category="groceries",
                        country="GB",
                        timestamp=DAY_TS,
                        txn_id="t-day",
                    ),
                    risk_score=RiskScore(
                        score=0.2,
                        decision=RiskDecision.ALLOW,
                        rationale="low",
                        strategy_name="rules-v1",
                        model_version="m:1",
                    ),
                ),
            )
        )
        record = json.loads(render(batch, JsonExportVisitor()))["transactions"][0]
        assert record["timestamp"] == DAY_TS.isoformat()


# ---------------------------------------------------------------------------
# ChannelRiskVisitor
# ---------------------------------------------------------------------------


class TestChannelRiskVisitor:
    def test_means_are_computed_per_channel(self, mixed_batch: ScoredBatch) -> None:
        result = render(mixed_batch, ChannelRiskVisitor())
        assert result["branch"] == {"mean_score": 0.10, "volume": 1.0}
        assert result["wire"]["volume"] == 2.0
        assert result["wire"]["mean_score"] == pytest.approx((0.55 + 0.95) / 2, abs=1e-6)

    def test_channels_are_ordered_by_descending_mean(self) -> None:
        batch = ScoredBatch(
            items=(
                make_scored("a", score=0.10, decision=RiskDecision.ALLOW, channel="branch"),
                make_scored("b", score=0.95, decision=RiskDecision.BLOCK, channel="wire"),
            )
        )
        assert list(render(batch, ChannelRiskVisitor())) == ["wire", "branch"]

    def test_empty_batch_yields_an_empty_report(self) -> None:
        assert render(ScoredBatch(items=()), ChannelRiskVisitor()) == {}

    def test_is_a_dataclass_visitor_with_independent_state(self, mixed_batch: ScoredBatch) -> None:
        """Each visitor instance keeps its own accumulators."""
        first, second = ChannelRiskVisitor(), ChannelRiskVisitor()
        render(mixed_batch, first)
        assert render(mixed_batch, second) == first.result()

    def test_added_without_touching_the_element_classes(self) -> None:
        """Extensibility evidence: a new report needs no change to the domain."""
        assert set(ChannelRiskVisitor.__mro__).__contains__(ReportVisitor)
        assert not hasattr(ScoredTransaction, "channel_report")
