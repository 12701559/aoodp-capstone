"""Tests for the Decorator pattern chain.

Each decorator is tested in isolation over a controlled inner component, and
then the assembled pipeline is tested as a whole.  The isolation tests are
what make the claim in the commentary checkable: a layer can be added,
removed or reordered without any other layer changing, because no layer
knows what it is wrapped in.
"""

from __future__ import annotations

import pytest

from capstone.decorators import (
    AuditingDecorator,
    CachingDecorator,
    FallbackDecorator,
    MetricsDecorator,
    RedactingDecorator,
    ScoringComponent,
    ScoringDecorator,
    StrategyComponent,
    compose_scoring_pipeline,
)
from capstone.domain import RiskDecision
from capstone.strategies import (
    DecisionPolicy,
    RuleBasedScoringStrategy,
    StrategyOutput,
)
from capstone.validation import ProviderUnavailableError, ValidationError
from capstone.tests.conftest import (
    SequenceClock,
    StubScoringComponent,
    StubScoringStrategy,
)

REVIEW_OUTPUT = StrategyOutput(
    normalised_score=0.6, rationale="stub decision", contributions={"is_foreign": 0.15}
)
ALLOW_OUTPUT = StrategyOutput(normalised_score=0.1, rationale="low risk", contributions={})


def _stub(output: StrategyOutput = REVIEW_OUTPUT, **kwargs) -> StubScoringComponent:
    return StubScoringComponent(output, **kwargs)


def _failing_strategy(error: Exception | None = None) -> StubScoringStrategy:
    """A real strategy whose provider is down -- the failure mode the
    fallback layer exists to absorb."""
    return StubScoringStrategy(
        name="hosted-llm", error=error or ProviderUnavailableError("simulated outage")
    )


# ---------------------------------------------------------------------------
# The component contract
# ---------------------------------------------------------------------------


class TestComponentContract:
    def test_a_strategy_component_adapts_a_strategy_and_a_policy(self) -> None:
        component = StrategyComponent(RuleBasedScoringStrategy())
        result = component.score({"is_foreign": 1.0}, model_ref="rules:1.0.0")

        assert result.score == pytest.approx(0.15)
        assert result.decision is RiskDecision.ALLOW
        assert result.strategy_name == "rules-v1"
        assert result.model_version == "rules:1.0.0"
        assert result.contributing_features == {"is_foreign>0.5": 0.15}

    def test_the_policy_is_data_and_can_be_swapped_without_touching_code(self) -> None:
        strict = StrategyComponent(
            RuleBasedScoringStrategy(), policy=DecisionPolicy(allow_below=0.01, block_at_or_above=0.1)
        )
        assert strict.score({"is_foreign": 1.0}).decision is RiskDecision.BLOCK

    def test_a_non_strategy_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            StrategyComponent("rules")  # type: ignore[arg-type]

    def test_base_decorator_delegates_by_default(self) -> None:
        inner = _stub()
        wrapper = ScoringDecorator(inner)
        assert wrapper.name == "stub-component"
        assert wrapper.score({}).score == REVIEW_OUTPUT.normalised_score
        assert wrapper.wrapped is inner

    def test_decorator_refuses_a_non_component(self) -> None:
        with pytest.raises(ValidationError):
            ScoringDecorator("not a component")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Security layer
# ---------------------------------------------------------------------------


class TestRedactingDecorator:
    def test_sensitive_values_are_masked_before_the_inner_layer_sees_them(self) -> None:
        inner = _stub()
        RedactingDecorator(inner).score(
            {"account_number": "ACC-00000001", "amount_zscore": 4.2}
        )
        assert inner.calls == [{"account_number": "********0001", "amount_zscore": 4.2}]

    def test_the_callers_mapping_is_not_mutated(self) -> None:
        features = {"account_number": "ACC-00000001"}
        RedactingDecorator(_stub()).score(features)
        assert features == {"account_number": "ACC-00000001"}

    def test_extra_sensitive_keys_can_be_declared(self) -> None:
        inner = _stub()
        RedactingDecorator(inner, extra_sensitive_keys=("device_id",)).score(
            {"device_id": "d-999"}
        )
        # The shared redaction rule masks all but the last four characters,
        # so a five-character identifier keeps its visible suffix.
        assert inner.calls == [{"device_id": "*-999"}]

    def test_name_records_the_layer(self) -> None:
        assert RedactingDecorator(_stub(name="core")).name == "redacting(core)"

    def test_model_reference_is_forwarded_unchanged(self) -> None:
        inner = _stub()
        RedactingDecorator(inner).score({}, model_ref="hosted-llm:2.1.0")
        assert inner.model_refs == ["hosted-llm:2.1.0"]


# ---------------------------------------------------------------------------
# 2. Observability layers
# ---------------------------------------------------------------------------


class TestAuditingDecorator:
    def test_one_structured_record_per_decision(self) -> None:
        from capstone.tests.conftest import CallRecorder

        sink = CallRecorder()
        AuditingDecorator(_stub(), sink=sink).score({"a": 1.0}, model_ref="rules:1.0.0")

        record = sink.calls[0][0][0]
        assert record["score"] == REVIEW_OUTPUT.normalised_score
        assert record["decision"] == "review"
        assert record["feature_count"] == 1
        assert record["model_version"] == "rules:1.0.0"

    def test_a_failing_component_is_not_audited_as_success(self) -> None:
        from capstone.tests.conftest import CallRecorder

        sink = CallRecorder()
        component = AuditingDecorator(
            _stub(error=ProviderUnavailableError("down")), sink=sink
        )
        with pytest.raises(ProviderUnavailableError):
            component.score({})
        assert sink.count == 0

    def test_default_sink_does_not_raise(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.INFO):
            AuditingDecorator(_stub()).score({"a": 1.0})
        assert any("score" in record.getMessage() for record in caplog.records)

    def test_name_records_the_layer(self) -> None:
        assert AuditingDecorator(_stub(name="core")).name == "audited(core)"


class TestMetricsDecorator:
    def test_counts_calls_latency_and_decisions_with_an_injected_clock(self) -> None:
        metrics = MetricsDecorator(_stub(), clock=SequenceClock([10.0, 10.25]))
        metrics.score({"a": 1.0})
        snapshot = metrics.snapshot()

        assert snapshot["calls"] == 1
        assert snapshot["errors"] == 0
        assert snapshot["mean_latency_seconds"] == pytest.approx(0.25)
        assert snapshot["max_latency_seconds"] == pytest.approx(0.25)
        assert snapshot["decisions"] == {"review": 1}

    def test_failures_are_counted_and_reraised(self) -> None:
        metrics = MetricsDecorator(
            _stub(error=ProviderUnavailableError("down")), clock=SequenceClock()
        )
        with pytest.raises(ProviderUnavailableError):
            metrics.score({})
        snapshot = metrics.snapshot()
        assert snapshot["errors"] == 1
        assert snapshot["calls"] == 0  # a failed call is not a scored call

    def test_name_records_the_layer(self) -> None:
        assert MetricsDecorator(_stub(name="core")).name == "metered(core)"


# ---------------------------------------------------------------------------
# 3. Efficiency layer
# ---------------------------------------------------------------------------


class TestCachingDecorator:
    def test_repeated_identical_vectors_hit_the_cache(self) -> None:
        inner = _stub()
        cache = CachingDecorator(inner, max_entries=4)
        cache.score({"a": 1.0})
        cache.score({"a": 1.0})

        assert len(inner.calls) == 1  # the inner component ran once
        assert cache.stats() == {"hits": 1, "misses": 1, "occupancy": 1}

    def test_different_vectors_miss(self) -> None:
        cache = CachingDecorator(_stub(), max_entries=4)
        cache.score({"a": 1.0})
        cache.score({"a": 2.0})
        assert cache.stats()["misses"] == 2

    def test_model_reference_is_part_of_the_key(self) -> None:
        inner = _stub()
        cache = CachingDecorator(inner, max_entries=4)
        cache.score({"a": 1.0}, model_ref="model-a")
        cache.score({"a": 1.0}, model_ref="model-b")
        assert len(inner.calls) == 2

    def test_the_cache_is_bounded(self) -> None:
        """Unbounded memoisation of transaction-derived data is both a leak
        and an unaudited secondary store of personal data."""
        cache = CachingDecorator(_stub(), max_entries=2)
        for value in (1.0, 2.0, 3.0):
            cache.score({"a": value})
        assert cache.stats()["occupancy"] == 2

    def test_zero_capacity_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CachingDecorator(_stub(), max_entries=0)

    def test_name_records_the_layer(self) -> None:
        assert CachingDecorator(_stub(name="core")).name == "cached(core)"


# ---------------------------------------------------------------------------
# 4. Resilience layer
# ---------------------------------------------------------------------------


class TestFallbackDecorator:
    def test_degrades_to_the_secondary_component_on_provider_failure(self) -> None:
        primary = StrategyComponent(_failing_strategy())
        fallback = StrategyComponent(RuleBasedScoringStrategy(), policy=DecisionPolicy())
        component = FallbackDecorator(primary, fallback)

        result = component.score({"is_foreign": 1.0})

        assert result.strategy_name == "rules-v1"
        assert result.rationale.startswith("[degraded: primary unavailable]")
        assert component.degradation_count() == 1

    def test_a_healthy_primary_is_used_and_no_degradation_is_recorded(self) -> None:
        fallback = StrategyComponent(RuleBasedScoringStrategy())
        component = FallbackDecorator(_stub(), fallback)
        component.score({})
        assert component.degradation_count() == 0

    def test_the_substitution_is_observable_to_the_operator(self) -> None:
        """A control that stops working silently is worse than no control."""
        from capstone.tests.conftest import CallRecorder

        alerts = CallRecorder()
        component = FallbackDecorator(
            _stub(error=ProviderUnavailableError("down")),
            StrategyComponent(RuleBasedScoringStrategy()),
            on_degrade=alerts,
        )
        component.score({})
        assert alerts.count == 1
        assert isinstance(alerts.calls[0][0][0], ProviderUnavailableError)

    def test_unexpected_errors_are_not_masked_by_the_fallback(self) -> None:
        """Only a provider outage is a known failure mode.  A programming
        error must surface rather than be hidden behind a degraded answer."""
        component = FallbackDecorator(
            _stub(error=RuntimeError("bug")), StrategyComponent(RuleBasedScoringStrategy())
        )
        with pytest.raises(RuntimeError):
            component.score({})

    def test_a_component_cannot_fall_back_to_itself(self) -> None:
        inner = _stub()
        with pytest.raises(ValidationError):
            FallbackDecorator(inner, inner)

    def test_non_component_fallback_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            FallbackDecorator(_stub(), "rules")  # type: ignore[arg-type]

    def test_name_records_both_sides_of_the_chain(self) -> None:
        component = FallbackDecorator(
            _stub(name="hosted"), StrategyComponent(RuleBasedScoringStrategy())
        )
        assert component.name == "fallback(hosted -> rules-v1)"


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


class TestPipelineComposition:
    def test_default_order_places_audit_outside_fallback(self) -> None:
        """The layer order is itself a governance decision, so it is asserted
        rather than left to whoever edits the assembly function next."""
        component = compose_scoring_pipeline(
            RuleBasedScoringStrategy(), fallback_strategy=RuleBasedScoringStrategy()
        )
        assert isinstance(component, RedactingDecorator)
        assert isinstance(component.wrapped, MetricsDecorator)
        assert isinstance(component.wrapped.wrapped, AuditingDecorator)
        assert isinstance(component.wrapped.wrapped.wrapped, FallbackDecorator)
        assert isinstance(component.wrapped.wrapped.wrapped.wrapped, CachingDecorator)
        assert isinstance(
            component.wrapped.wrapped.wrapped.wrapped.wrapped, StrategyComponent
        )

    def test_every_layer_is_optional(self) -> None:
        component = compose_scoring_pipeline(
            RuleBasedScoringStrategy(), cache_entries=None, fallback_strategy=None
        )
        assert "cached" not in component.name
        assert "fallback" not in component.name

    def test_redaction_still_applies_without_a_cache_or_fallback(self) -> None:
        component = compose_scoring_pipeline(
            RuleBasedScoringStrategy(), cache_entries=None, fallback_strategy=None
        )
        assert component.name.startswith("redacting(")

    def test_the_assembled_pipeline_scores_a_vector(self) -> None:
        sink: list[dict] = []
        component = compose_scoring_pipeline(
            RuleBasedScoringStrategy(),
            fallback_strategy=RuleBasedScoringStrategy(),
            audit_sink=sink.append,
            clock=SequenceClock([0.0, 0.5]),
        )
        result = component.score({"is_foreign": 1.0}, model_ref="rules:transaction-risk:2.1.0")

        assert result.decision is RiskDecision.ALLOW
        assert result.model_version == "rules:transaction-risk:2.1.0"
        assert sink[0]["decision"] == "allow"

    def test_degradation_still_audits_the_decision_the_caller_receives(self) -> None:
        sink: list[dict] = []
        component = compose_scoring_pipeline(
            _failing_strategy(),
            fallback_strategy=RuleBasedScoringStrategy(),
            audit_sink=sink.append,
        )
        result = component.score({"is_foreign": 1.0})
        assert result.rationale.startswith("[degraded")
        # Exactly one record, and it is the degraded decision the caller
        # received -- which is only true because the auditor wraps the
        # fallback instead of sitting inside it.
        assert len(sink) == 1
        assert sink[0]["rationale"].startswith("[degraded")

    def test_a_decorated_component_is_still_a_component(self) -> None:
        assert isinstance(
            compose_scoring_pipeline(RuleBasedScoringStrategy()), ScoringComponent
        )
