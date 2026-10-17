"""Tests for the Strategy pattern and the ports it depends on.

Two themes run through this file:

1. **Substitutability.**  Every algorithm honours the same contract
   (``score(features, model_ref) -> StrategyOutput``), so callers never
   branch on which one they hold.  The tests assert that contract, not the
   internal arithmetic of any one of them.
2. **The provider reply is untrusted input.**  The model-backed strategy
   validates everything a remote service could send, and each malformed
   shape has its own test, because "the provider will always return a
   well-formed payload" is exactly the assumption that fails in production.
"""

from __future__ import annotations

import math

import pytest

from capstone.strategies import (
    DEFAULT_RULES,
    DecisionPolicy,
    ModelBackedScoringStrategy,
    RiskScoringStrategy,
    Rule,
    RuleBasedScoringStrategy,
    StrategyOutput,
    WeightedStatisticalStrategy,
)
from capstone.validation import ProviderUnavailableError, ValidationError
from capstone.tests.conftest import FailingBackend, RecordingBackend


# ---------------------------------------------------------------------------
# Rule value object
# ---------------------------------------------------------------------------


class TestRule:
    @pytest.mark.parametrize(
        "comparison,threshold,value,expected",
        [
            (">", 3.0, 4.0, True),
            (">", 3.0, 3.0, False),
            (">=", 3.0, 3.0, True),
            ("<", 3.0, 2.0, True),
            ("<", 3.0, 3.0, False),
            ("<=", 3.0, 3.0, True),
            ("==", 3.0, 3.0, True),
            ("==", 3.0, 3.1, False),
        ],
    )
    def test_comparisons(self, comparison, threshold, value, expected) -> None:
        rule = Rule("f", threshold, 0.1, comparison, "why")
        assert rule.fires(value) is expected

    def test_unsupported_comparison_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            Rule("f", 1.0, 0.1, "!=", "why")

    @pytest.mark.parametrize("weight", [-0.1, 1.1])
    def test_weight_must_be_a_probability_mass(self, weight: float) -> None:
        with pytest.raises(ValidationError):
            Rule("f", 1.0, weight, ">", "why")


# ---------------------------------------------------------------------------
# Strategy 1: deterministic rules
# ---------------------------------------------------------------------------


class TestRuleBasedStrategy:
    def test_fires_the_expected_rules_for_a_high_risk_vector(self) -> None:
        features = {
            "amount_zscore": 23.0,
            "is_foreign": 1.0,
            "is_elevated_category": 1.0,
            "is_odd_hour": 1.0,
            "channel_risk": 0.80,
            "velocity": 14.0,
            "log_amount": 7.131699,
        }
        outcome = RuleBasedScoringStrategy().score(features)
        # 0.30 + 0.15 + 0.20 + 0.10 + 0.10 + 0.25 + 0.05 = 1.15, clamped to 1.0
        assert outcome.normalised_score == 1.0
        assert len(outcome.contributions) == 7
        assert "7 rule(s) fired" in outcome.rationale

    def test_no_fired_rule_yields_zero_and_says_so(self) -> None:
        features = {
            "amount_zscore": -1.83,
            "is_foreign": 0.0,
            "is_elevated_category": 0.0,
            "is_odd_hour": 0.0,
            "channel_risk": 0.05,
            "velocity": 0.25,
            "log_amount": 2.251291,
        }
        outcome = RuleBasedScoringStrategy().score(features)
        assert outcome.normalised_score == 0.0
        assert outcome.contributions == {}
        assert outcome.rationale == "no rules fired"

    def test_partial_firing_sums_inspectable_weights(self) -> None:
        outcome = RuleBasedScoringStrategy().score(
            {"is_foreign": 1.0, "channel_risk": 0.7}
        )
        assert outcome.normalised_score == pytest.approx(0.25)
        assert outcome.contributions == {"is_foreign>0.5": 0.15, "channel_risk>0.6": 0.10}

    def test_a_missing_feature_is_skipped_not_scored_as_zero(self) -> None:
        """Scoring an absent feature as 0.0 would silently weaken the rule;
        skipping it is recorded by its absence from the contributions."""
        outcome = RuleBasedScoringStrategy().score({"is_foreign": 1.0})
        assert outcome.normalised_score == pytest.approx(0.15)
        assert set(outcome.contributions) == {"is_foreign>0.5"}

    def test_custom_rule_set_replaces_the_defaults(self) -> None:
        strategy = RuleBasedScoringStrategy([Rule("is_foreign", 0.5, 0.9, ">", "abroad")])
        assert strategy.rules == (Rule("is_foreign", 0.5, 0.9, ">", "abroad"),)
        assert strategy.score({"is_foreign": 1.0}).normalised_score == pytest.approx(0.9)

    def test_is_a_strategy_and_reports_its_name(self) -> None:
        strategy = RuleBasedScoringStrategy()
        assert isinstance(strategy, RiskScoringStrategy)
        assert strategy.name == "rules-v1"

    def test_model_ref_is_not_required_but_is_accepted(self) -> None:
        """A deterministic strategy ignores the model reference yet still
        satisfies the same signature -- that substitutability is the point."""
        assert RuleBasedScoringStrategy().score({}, model_ref="ignored").normalised_score == 0.0

    def test_default_rules_are_all_well_formed(self) -> None:
        assert all(isinstance(rule, Rule) for rule in DEFAULT_RULES)
        assert len({rule.feature for rule in DEFAULT_RULES}) == len(DEFAULT_RULES)


# ---------------------------------------------------------------------------
# Strategy 2: weighted statistical scorecard
# ---------------------------------------------------------------------------


class TestWeightedStatisticalStrategy:
    def test_sigmoid_at_zero_bias_is_one_half(self) -> None:
        strategy = WeightedStatisticalStrategy({"a": 1.0}, bias=0.0)
        outcome = strategy.score({"a": 0.0})
        assert outcome.normalised_score == pytest.approx(0.5)

    def test_matches_the_closed_form_sigmoid(self) -> None:
        strategy = WeightedStatisticalStrategy({"a": 2.0, "b": -1.0}, bias=0.5)
        features = {"a": 1.0, "b": 1.0}
        expected = 1.0 / (1.0 + math.exp(-(0.5 + 2.0 - 1.0)))
        assert strategy.score(features).normalised_score == pytest.approx(
            round(expected, 6)
        )

    def test_contributions_cover_every_weighted_feature(self) -> None:
        strategy = WeightedStatisticalStrategy({"a": 2.0, "b": -1.0})
        outcome = strategy.score({"a": 3.0, "b": 1.0})
        assert set(outcome.contributions) == {"a", "b"}
        assert outcome.contributions["a"] == pytest.approx(6.0)
        assert outcome.contributions["b"] == pytest.approx(-1.0)

    def test_absent_feature_contributes_nothing(self) -> None:
        strategy = WeightedStatisticalStrategy({"a": 1.0, "b": 1.0}, bias=0.0)
        assert strategy.score({"a": 1.0}).contributions["b"] == 0.0

    def test_extreme_values_stay_inside_the_unit_interval(self) -> None:
        strategy = WeightedStatisticalStrategy({"a": 1000.0}, bias=0.0)
        outcome = strategy.score({"a": 1000.0})
        assert 0.0 <= outcome.normalised_score <= 1.0

    def test_weights_property_returns_a_copy(self) -> None:
        strategy = WeightedStatisticalStrategy({"a": 1.0})
        strategy.weights["a"] = 99.0  # mutating the copy must not affect the model
        assert strategy.weights["a"] == 1.0

    def test_empty_weight_map_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            WeightedStatisticalStrategy({})

    def test_non_finite_bias_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            WeightedStatisticalStrategy({"a": 1.0}, bias=float("inf"))


# ---------------------------------------------------------------------------
# Strategy 3: the AI path
# ---------------------------------------------------------------------------


class TestModelBackedStrategy:
    def test_delegates_to_the_injected_backend_with_the_agreed_contract(self) -> None:
        backend = RecordingBackend(score=0.77, rationale="model says so")
        strategy = ModelBackedScoringStrategy(backend)
        outcome = strategy.score({"is_foreign": 1.0}, model_ref="transaction-risk:2.1.0")

        assert outcome.normalised_score == pytest.approx(0.77)
        assert outcome.rationale == "model says so"
        assert backend.seen == [
            {"features": {"is_foreign": 1.0}, "model_ref": "transaction-risk:2.1.0"}
        ]

    def test_backend_is_exposed_for_contract_assertions(self) -> None:
        backend = RecordingBackend()
        assert ModelBackedScoringStrategy(backend).backend is backend

    def test_a_non_backend_is_refused_at_construction(self) -> None:
        with pytest.raises(ValidationError):
            ModelBackedScoringStrategy("not a backend")  # type: ignore[arg-type]

    def test_probability_key_is_accepted_as_the_score(self) -> None:
        outcome = ModelBackedScoringStrategy(RecordingBackend(score=0.25)).score({})
        assert outcome.normalised_score == pytest.approx(0.25)

    def test_absent_rationale_is_synthesised_from_the_score(self) -> None:
        class NoExplanation(RecordingBackend):
            def predict(self, features, model_ref):  # type: ignore[override]
                return {"score": 0.33}

        outcome = ModelBackedScoringStrategy(NoExplanation()).score(
            {}, model_ref="hosted-llm:2.1.0"
        )
        assert "hosted-llm:2.1.0" in outcome.rationale
        assert "0.3300" in outcome.rationale

    @pytest.mark.parametrize(
        "payload",
        [
            {},  # nothing at all
            {"probability": None},  # no usable score
            {"score": "high"},  # a string where a number belongs
            {"score": True},  # bool is numeric in Python but not a probability
            {"score": 1.5},  # outside the unit interval
            {"score": -0.2},  # outside the unit interval
            {"score": 0.5, "contributions": "none"},  # wrong contributions type
        ],
    )
    def test_malformed_provider_replies_are_refused(self, payload: dict) -> None:
        """A remote reply is untrusted input: coercing it into a plausible
        decision would turn a provider fault into an unexplained denial."""
        class Canned(RecordingBackend):
            def predict(self, features, model_ref):  # type: ignore[override]
                return payload

        with pytest.raises(ProviderUnavailableError):
            ModelBackedScoringStrategy(Canned()).score({})

    def test_non_mapping_reply_is_refused(self) -> None:
        class NotAMapping:
            def predict(self, features, model_ref):  # type: ignore[override]
                return ["score", 0.5]

        with pytest.raises(ProviderUnavailableError):
            ModelBackedScoringStrategy(NotAMapping()).score({})  # type: ignore[arg-type]

    def test_non_numeric_contributions_are_dropped_not_coerced(self) -> None:
        class Mixed(RecordingBackend):
            def predict(self, features, model_ref):  # type: ignore[override]
                return {
                    "score": 0.5,
                    "contributions": {
                        "good": 0.3,
                        "bad": "lots",
                        "flag": True,
                    },
                }

        outcome = ModelBackedScoringStrategy(Mixed()).score({})
        assert outcome.contributions == {"good": 0.3}

    def test_third_party_exceptions_are_translated_without_leaking_detail(self) -> None:
        backend = FailingBackend(ValueError("vendor stack trace detail"))
        with pytest.raises(ProviderUnavailableError) as excinfo:
            ModelBackedScoringStrategy(backend).score({})
        assert "vendor stack trace detail" not in str(excinfo.value)
        assert "ValueError" in str(excinfo.value)

    def test_domain_provider_errors_pass_through_unchanged(self) -> None:
        original = ProviderUnavailableError("provider is down")
        with pytest.raises(ProviderUnavailableError) as excinfo:
            ModelBackedScoringStrategy(FailingBackend(original)).score({})
        assert excinfo.value is original

    def test_outage_is_surfaced_rather_than_scored_as_a_default(self) -> None:
        backend = FailingBackend()
        with pytest.raises(ProviderUnavailableError):
            ModelBackedScoringStrategy(backend).score({})
        assert backend.attempts == 1


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------


class TestDecisionPolicy:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.0, "allow"),
            (0.4499, "allow"),
            (0.45, "review"),
            (0.7999, "review"),
            (0.80, "block"),
            (1.0, "block"),
        ],
    )
    def test_threshold_boundaries(self, score: float, expected: str) -> None:
        assert DecisionPolicy().decide(score).value == expected

    def test_inverted_thresholds_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            DecisionPolicy(allow_below=0.9, block_at_or_above=0.5)

    def test_equal_thresholds_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            DecisionPolicy(allow_below=0.5, block_at_or_above=0.5)

    def test_policy_is_data_and_is_immutable(self) -> None:
        from dataclasses import FrozenInstanceError

        policy = DecisionPolicy()
        with pytest.raises(FrozenInstanceError):
            policy.allow_below = 0.1  # type: ignore[misc]


class TestStrategyOutput:
    @pytest.mark.parametrize("score", [-0.1, 1.1, float("nan")])
    def test_score_must_be_normalised(self, score: float) -> None:
        with pytest.raises(ValidationError):
            StrategyOutput(normalised_score=score, rationale="why")

    @pytest.mark.parametrize("rationale", ["", "   ", None])
    def test_rationale_is_mandatory(self, rationale: object) -> None:
        with pytest.raises(ValidationError):
            StrategyOutput(normalised_score=0.5, rationale=rationale)  # type: ignore[arg-type]

    def test_every_strategy_produces_an_explanation(self) -> None:
        """The explainability contract: no algorithm may return a bare
        number, because an unexplained automated decision about a person
        cannot be defended to that person or to a regulator."""
        strategies = [
            RuleBasedScoringStrategy(),
            WeightedStatisticalStrategy({"is_foreign": 1.0}),
            ModelBackedScoringStrategy(RecordingBackend()),
        ]
        for strategy in strategies:
            outcome = strategy.score({"is_foreign": 1.0})
            assert outcome.rationale.strip()
            assert isinstance(outcome.contributions, dict)
