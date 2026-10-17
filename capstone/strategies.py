"""
Strategy pattern -- interchangeable risk-scoring algorithms.

The problem
-----------
Risk scoring has several legitimate algorithms and the right one changes
with context.  A rules-based engine is what a regulator can inspect line by
line; a weighted statistical model is what performs better on historic data;
a hosted large-language-model endpoint is what the organisation may want
for narrative explanation of unusual cases.  A single ``score()`` method
containing ``if self.mode == "rules": ... elif ...`` grows a branch for every
combination and must be reopened and retested for every new algorithm.

The pattern
-----------
``RiskScoringStrategy`` declares the family.  Each concrete strategy
encapsulates exactly one algorithm and is substitutable for any other.  The
caller holds an abstraction, never a concrete class, so the algorithm can be
chosen at runtime -- from a config value, a feature flag, or an A/B
experiment -- without touching one line of the calling code.  Adding an
algorithm means adding a class, not editing an existing one: the
Open/Closed Principle applied literally.

Separation of concerns
----------------------
A strategy returns a *normalised score plus its contributions*.  It does
**not** decide the outcome.  Thresholds live in :class:`DecisionPolicy`,
which is a policy concern owned by the business and revised on a different
schedule from the algorithm.  Conflating the two would mean a change to a
cut-off requires retesting the maths, which is exactly the coupling this
design avoids.

Testability
-----------
Because :class:`ModelBackedScoringStrategy` depends on the
:class:`ScoringBackend` *protocol* rather than any SDK, the AI path is
tested with a stub.  No network, no credentials, no live model, no cost --
which is the enabling mechanism for the mocking artefact.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from .domain import RiskDecision
from .validation import (
    ProviderUnavailableError,
    ValidationError,
    validate_unit_interval,
    validate_weight_map,
)


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyOutput:
    """What every strategy must return: a value, a reason, and the workings.

    ``contributions`` is not optional decoration.  It is the field that makes
    an AI-assisted decision *auditable*: it records which inputs actually
    drove the number, so a declined customer can be given a reason and an
    auditor can reconstruct the decision without the model.
    """

    normalised_score: float
    rationale: str
    contributions: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_unit_interval(self.normalised_score)
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValidationError("rationale must be a non-empty string")


@dataclass(frozen=True, slots=True)
class DecisionPolicy:
    """Maps a normalised score onto a decision.

    Held as data so the policy is inspectable and version-controllable
    independently of the algorithms, and so a policy change is a
    configuration change rather than a code change.
    """

    allow_below: float = 0.45
    block_at_or_above: float = 0.80

    def __post_init__(self) -> None:
        validate_unit_interval(self.allow_below, "allow_below")
        validate_unit_interval(self.block_at_or_above, "block_at_or_above")
        if self.allow_below >= self.block_at_or_above:
            raise ValidationError("allow_below must be strictly less than block_at_or_above")

    def decide(self, score: float) -> RiskDecision:
        """Return the decision implied by *score*."""
        if score >= self.block_at_or_above:
            return RiskDecision.BLOCK
        if score >= self.allow_below:
            return RiskDecision.REVIEW
        return RiskDecision.ALLOW


@runtime_checkable
class ScoringBackend(Protocol):
    """The port through which a strategy reaches an external AI model.

    Declaring a ``Protocol`` rather than inheriting from a concrete client
    class is the Dependency Inversion Principle in its practical form:
    high-level scoring policy depends on this narrow abstraction, and the
    low-level provider (local heuristic, hosted LLM, vendor SDK) depends on
    it too.  Neither knows the other exists, so either can be replaced, and
    a test can substitute a three-line fake for a network call.
    """

    def predict(
        self, features: Mapping[str, float], model_ref: str
    ) -> Mapping[str, Any]:
        """Return a raw prediction payload for *features*."""
        ...  # pragma: no cover - protocol declaration


# ---------------------------------------------------------------------------
# The strategy family
# ---------------------------------------------------------------------------


class RiskScoringStrategy(ABC):
    """Abstract base for every interchangeable scoring algorithm."""

    name: str = "unnamed-strategy"

    @abstractmethod
    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> StrategyOutput:
        """Score a feature vector and explain the result."""
        raise NotImplementedError


# -- 1. Deterministic rules --------------------------------------------------

@dataclass(frozen=True, slots=True)
class Rule:
    """One inspectable ``if feature comparison threshold then weight`` rule."""

    feature: str
    threshold: float
    weight: float
    comparison: str = ">"
    description: str = ""

    def __post_init__(self) -> None:
        if self.comparison not in {">", ">=", "<", "<=", "=="}:
            raise ValidationError(f"unsupported comparison {self.comparison!r}")
        if not 0.0 <= self.weight <= 1.0:
            raise ValidationError("rule weight must be within [0, 1]")

    def fires(self, value: float) -> bool:
        """Return whether this rule is triggered by *value*."""
        if self.comparison == ">":
            return value > self.threshold
        if self.comparison == ">=":
            return value >= self.threshold
        if self.comparison == "<":
            return value < self.threshold
        if self.comparison == "<=":
            return value <= self.threshold
        return value == self.threshold


class RuleBasedScoringStrategy(RiskScoringStrategy):
    """Sums the weights of every fired rule, clamped to a normalised score.

    Chosen as the default because it is fully explainable: the rationale it
    returns *is* the decision, and a non-programmer can audit it.  Its
    weakness is that weights are expert-set rather than learned, and it
    cannot express interaction effects between features -- which is precisely
    why the statistical and model-backed strategies exist alongside it.
    """

    name = "rules-v1"

    def __init__(self, rules: Sequence[Rule] | None = None) -> None:
        self._rules: tuple[Rule, ...] = tuple(rules) if rules is not None else DEFAULT_RULES

    @property
    def rules(self) -> tuple[Rule, ...]:
        """Return the rule set (immutable, for inspection and testing)."""
        return self._rules

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> StrategyOutput:
        fired: list[Rule] = []
        contributions: dict[str, float] = {}

        for rule in self._rules:
            value = features.get(rule.feature)
            if value is None:
                # A missing feature is not scored as zero: doing so would
                # silently weaken the rule.  It is skipped and simply does
                # not contribute, which is recorded by its absence from
                # the contributions map.
                continue
            if rule.fires(float(value)):
                fired.append(rule)
                contributions[f"{rule.feature}{rule.comparison}{rule.threshold}"] = rule.weight

        raw = sum(rule.weight for rule in fired)
        score = min(1.0, raw)  # clamp: weights may legitimately sum above 1

        if fired:
            detail = "; ".join(
                f"{rule.feature}{rule.comparison}{rule.threshold}"
                f" (+{rule.weight:.2f}) {rule.description}".strip()
                for rule in fired
            )
            rationale = f"{len(fired)} rule(s) fired: {detail}"
        else:
            rationale = "no rules fired"

        return StrategyOutput(
            normalised_score=score, rationale=rationale, contributions=contributions
        )


DEFAULT_RULES: tuple[Rule, ...] = (
    Rule("amount_zscore", 3.0, 0.30, ">", "amount far above the account's own norm"),
    Rule("is_foreign", 0.5, 0.15, ">", "transaction originates abroad"),
    Rule("is_elevated_category", 0.5, 0.20, ">", "elevated-risk merchant category"),
    Rule("is_odd_hour", 0.5, 0.10, ">", "unusual hour of day"),
    Rule("channel_risk", 0.60, 0.10, ">", "higher-risk origination channel"),
    Rule("velocity", 10.0, 0.25, ">", "unusual transaction velocity on the account"),
    Rule("log_amount", 6.0, 0.05, ">", "large absolute amount"),
)


# -- 2. Weighted statistical model -------------------------------------------


class WeightedStatisticalStrategy(RiskScoringStrategy):
    """Logistic scoring over a weight vector: ``sigmoid(w . x + b)``.

    This is the shape of a real credit/fraud scorecard.  It is included
    because it demonstrates the trade-off the portfolio must discuss: the
    weights can be learned from data, so accuracy is higher than hand-set
    rules, but the *meaning* of the score is no longer self-evident -- it is
    only interpretable through its per-feature contributions, which is why
    every strategy is contractually obliged to return them.
    """

    name = "statistical-v1"

    def __init__(self, weights: Mapping[str, float], bias: float = -1.5) -> None:
        self._weights = validate_weight_map(weights)
        if not math.isfinite(float(bias)):
            raise ValidationError("bias must be finite")
        self._bias = float(bias)

    @property
    def weights(self) -> Mapping[str, float]:
        """Return the weight vector (read-only copy)."""
        return dict(self._weights)

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> StrategyOutput:
        contributions: dict[str, float] = {}
        total = self._bias

        for feature, weight in self._weights.items():
            value = float(features.get(feature, 0.0))
            contribution = weight * value
            contributions[feature] = round(contribution, 6)
            total += contribution

        score = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, total))))
        score = min(1.0, max(0.0, round(score, 6)))

        top = sorted(
            ((k, v) for k, v in contributions.items() if v > 0),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        if top:
            drivers = ", ".join(f"{name} (+{value:.3f})" for name, value in top)
            rationale = f"logistic score {score:.4f}; principal drivers: {drivers}"
        else:
            rationale = f"logistic score {score:.4f}; no feature increased risk"

        return StrategyOutput(
            normalised_score=score, rationale=rationale, contributions=contributions
        )


# -- 3. Model-backed (the AI path) -------------------------------------------


class ModelBackedScoringStrategy(RiskScoringStrategy):
    """Delegates scoring to an external AI model through the narrow port.

    The strategy itself contains no model logic and no provider SDK.  It
    validates the provider's reply, which is the security-critical step: a
    remote response is untrusted input, so a missing key, a non-numeric
    value or a score outside ``[0, 1]`` must raise rather than be coerced
    into a plausible-looking decision.
    """

    name = "model-backed-v1"

    def __init__(self, backend: ScoringBackend) -> None:
        if not isinstance(backend, ScoringBackend):
            raise ValidationError("backend must implement the ScoringBackend protocol")
        self._backend = backend

    @property
    def backend(self) -> ScoringBackend:
        """Expose the port (used by tests to assert the call contract)."""
        return self._backend

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> StrategyOutput:
        try:
            payload = self._backend.predict(features=dict(features), model_ref=model_ref)
        except ProviderUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberate translation boundary
            # Third-party exceptions are translated into the domain's own
            # error type so no vendor detail leaks upward and the caller has
            # exactly one error to handle.
            raise ProviderUnavailableError(
                f"scoring backend failed: {type(exc).__name__}"
            ) from exc

        if not isinstance(payload, Mapping):
            raise ProviderUnavailableError("backend returned a non-mapping payload")

        raw_score = payload.get("score", payload.get("probability"))
        if raw_score is None:
            raise ProviderUnavailableError("backend payload omitted a score field")
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise ProviderUnavailableError("backend score was not numeric")

        contributions = payload.get("contributions") or {}
        if not isinstance(contributions, Mapping):
            raise ProviderUnavailableError("backend contributions were not a mapping")

        rationale = payload.get("explanation") or payload.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = f"model {model_ref} returned score {float(raw_score):.4f}"

        try:
            normalised = validate_unit_interval(float(raw_score))
        except ValidationError as exc:
            # A reply outside [0, 1] is a misbehaving provider, not a bad
            # caller: it is translated into the provider error so the
            # fallback layer can degrade the decision instead of the whole
            # request failing with a validation error that blames the wrong
            # party.
            raise ProviderUnavailableError(
                f"backend score was outside the unit interval: {raw_score!r}"
            ) from exc

        return StrategyOutput(
            normalised_score=normalised,
            rationale=rationale,
            contributions={
                str(k): float(v)
                for k, v in contributions.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            },
        )


__all__ = [
    "DEFAULT_RULES",
    "DecisionPolicy",
    "ModelBackedScoringStrategy",
    "RiskScoringStrategy",
    "Rule",
    "RuleBasedScoringStrategy",
    "ScoringBackend",
    "StrategyOutput",
    "WeightedStatisticalStrategy",
]
