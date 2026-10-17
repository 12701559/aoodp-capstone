"""
Decorator pattern -- attaching cross-cutting behaviour at runtime.

The problem
-----------
Auditing, timing, PII redaction, caching and graceful degradation are all
required, and none of them is the *algorithm*.  Writing them into
``StrategyComponent.score()`` produces a method that does five jobs, cannot
be tested independently, and forces every new algorithm to inherit
behaviour it may not want.  Subclassing per combination compounds the
problem: audit + cache + fallback, then audit + cache, and so on.

The pattern
-----------
Every decorator implements the same :class:`ScoringComponent` interface as
the object it wraps, and holds a reference to that object.  Because the
decorator *is* a component, decorators stack, and the wrapping order is
chosen at composition time -- i.e. the system's behaviour is decided at
runtime rather than fixed at compile time.

Two properties make this safe and useful:

* **Substitutability.**  A decorated component satisfies the same contract
  as the undecorated one, so the caller cannot tell (and must not need to)
  how many layers are present.  This is the Liskov Substitution Principle
  doing real work rather than being asserted in a report.
* **Ordering is a design decision with consequences.**  Redaction must sit
  *outside* any layer that transmits data off-process, or the audit record
  will contain the unredacted payload.  ``compose_scoring_pipeline``
  documents and enforces a sane default order; the tests assert the order
  actually holds, because ordering bugs here are silent.

Trade-off worth stating explicitly in the portfolio: decorators make
behaviour composable at the cost of indirection.  A stack of five
decorators produces stack traces and profiling output that no longer
correspond to the class the reader is looking at, which is a real
maintenance cost -- hence the small, well-named set below rather than a
decorator for every possible concern.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any, Callable, Mapping, Sequence

from .domain import RiskDecision, RiskScore
from .strategies import (
    DecisionPolicy,
    RiskScoringStrategy,
    ScoringBackend,
    StrategyOutput,
)
from .validation import ProviderUnavailableError, ValidationError, redact_mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Component contract
# ---------------------------------------------------------------------------


class ScoringComponent(ABC):
    """The single contract every scoring object -- decorated or not -- honours."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a human-readable description of the assembled pipeline."""
        raise NotImplementedError

    @abstractmethod
    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        """Score a validated feature vector and return an auditable result."""
        raise NotImplementedError


class StrategyComponent(ScoringComponent):
    """The undecorated core: adapts a :class:`RiskScoringStrategy` to a component.

    This is the object every decorator wraps.  It holds the algorithm and
    the policy, and does nothing else -- no logging, no timing, no caching.
    """

    def __init__(
        self,
        strategy: RiskScoringStrategy,
        policy: DecisionPolicy | None = None,
    ) -> None:
        if not isinstance(strategy, RiskScoringStrategy):
            raise ValidationError("strategy must be a RiskScoringStrategy")
        self._strategy = strategy
        self._policy = policy or DecisionPolicy()

    @property
    def name(self) -> str:
        return self._strategy.name

    @property
    def strategy(self) -> RiskScoringStrategy:
        """Expose the wrapped strategy for inspection and testing."""
        return self._strategy

    @property
    def policy(self) -> DecisionPolicy:
        """Expose the decision policy."""
        return self._policy

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        """Run the strategy and attach the policy's decision and provenance."""
        outcome: StrategyOutput = self._strategy.score(features, model_ref=model_ref)
        return RiskScore(
            score=outcome.normalised_score,
            decision=self._policy.decide(outcome.normalised_score),
            rationale=outcome.rationale,
            strategy_name=self._strategy.name,
            model_version=model_ref,
            contributing_features=dict(outcome.contributions),
        )


# ---------------------------------------------------------------------------
# Base decorator
# ---------------------------------------------------------------------------


class ScoringDecorator(ScoringComponent):
    """Base class holding the wrapped component and delegating by default.

    Subclasses override only the behaviour they add and call ``super()`` to
    continue down the chain, so each decorator is ten to thirty lines and is
    unit-testable in isolation with a stub component.
    """

    def __init__(self, wrapped: ScoringComponent) -> None:
        if not isinstance(wrapped, ScoringComponent):
            raise ValidationError("wrapped must be a ScoringComponent")
        self._wrapped = wrapped

    @property
    def wrapped(self) -> ScoringComponent:
        """Return the next component inward in the chain."""
        return self._wrapped

    @property
    def name(self) -> str:
        return self._wrapped.name

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        return self._wrapped.score(features, model_ref=model_ref)


# ---------------------------------------------------------------------------
# 1. Security layer: redaction
# ---------------------------------------------------------------------------


class RedactingDecorator(ScoringDecorator):
    """Redacts sensitive keys from the feature payload before it travels inward.

    This is a **security** control, not tidiness.  If the pipeline is
    assembled over a hosted model provider, the features are sent to a third
    party; anything identifying the account holder is personal data leaving
    the organisation's boundary.  Placing redaction as the outermost
    decorator guarantees no inner layer -- including the audit sink -- ever
    observes the raw identifier.

    The wrapped component receives a **new** mapping, so it cannot hold a
    reference to, or mutate, the caller's original dictionary.
    """

    def __init__(
        self,
        wrapped: ScoringComponent,
        extra_sensitive_keys: Sequence[str] = (),
    ) -> None:
        super().__init__(wrapped)
        self._extra = tuple(extra_sensitive_keys)

    @property
    def name(self) -> str:
        return f"redacting({self._wrapped.name})"

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        safe_features = redact_mapping(features, extra_sensitive=self._extra)
        return self._wrapped.score(safe_features, model_ref=model_ref)


# ---------------------------------------------------------------------------
# 2. Observability layer: audit logging
# ---------------------------------------------------------------------------


class AuditingDecorator(ScoringDecorator):
    """Writes one structured audit line per scoring decision.

    The sink is injected as a callable rather than taken from the ``logging``
    module directly.  Two benefits follow: the test asserts the *content* of
    the audit record without parsing log output, and the same decorator can
    write to a file, a database table or a message queue with no change to
    this class -- dependency inversion applied to a cross-cutting concern.
    """

    def __init__(
        self,
        wrapped: ScoringComponent,
        sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(wrapped)
        self._sink = sink or self._default_sink
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"audited({self._wrapped.name})"

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        result = self._wrapped.score(features, model_ref=model_ref)
        record = {
            "component": self._wrapped.name,
            "model_version": model_ref,
            "feature_count": len(features),
            "score": result.score,
            "decision": result.decision.value,
            "rationale": result.rationale,
        }
        with self._lock:
            self._sink(record)
        return result

    @staticmethod
    def _default_sink(record: dict[str, Any]) -> None:
        """Fall back to the module logger when no sink is supplied."""
        logger.info("risk-decision %s", record)


# ---------------------------------------------------------------------------
# 3. Performance layer: metrics
# ---------------------------------------------------------------------------


class MetricsDecorator(ScoringDecorator):
    """Records call counts, decision counts and latency percentiles.

    The clock is injected so latency assertions in the tests are
    deterministic instead of flaky: a fake clock returns a fixed sequence of
    timestamps and the recorded duration is exact.  A decorator that called
    ``time.perf_counter()`` directly would be untestable without sleeping.
    """

    def __init__(
        self,
        wrapped: ScoringComponent,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(wrapped)
        self._clock = clock or time.perf_counter
        self._lock = threading.Lock()
        self._calls = 0
        self._errors = 0
        self._total_seconds = 0.0
        self._max_seconds = 0.0
        self._decisions: dict[str, int] = {}

    @property
    def name(self) -> str:
        return f"metered({self._wrapped.name})"

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        started = self._clock()
        try:
            result = self._wrapped.score(features, model_ref=model_ref)
        except Exception:
            with self._lock:
                self._errors += 1
            raise
        elapsed = self._clock() - started
        with self._lock:
            self._calls += 1
            self._total_seconds += elapsed
            self._max_seconds = max(self._max_seconds, elapsed)
            key = result.decision.value
            self._decisions[key] = self._decisions.get(key, 0) + 1
        return result

    def snapshot(self) -> dict[str, float | int | dict[str, int]]:
        """Return a copy of the collected counters."""
        with self._lock:
            mean = self._total_seconds / self._calls if self._calls else 0.0
            return {
                "calls": self._calls,
                "errors": self._errors,
                "mean_latency_seconds": round(mean, 6),
                "max_latency_seconds": round(self._max_seconds, 6),
                "decisions": dict(self._decisions),
            }


# ---------------------------------------------------------------------------
# 4. Cost/efficiency layer: bounded cache
# ---------------------------------------------------------------------------


class CachingDecorator(ScoringDecorator):
    """Memoises scores for repeated identical feature vectors.

    Bounded with an LRU policy rather than an unbounded dictionary: an
    unbounded cache keyed on transaction-derived data is both a memory leak
    and, in a system that must retain or delete personal data on request, an
    unaudited secondary store of derived personal information.  The bound is
    therefore a data-protection control as much as a performance one.
    """

    def __init__(self, wrapped: ScoringComponent, max_entries: int = 256) -> None:
        super().__init__(wrapped)
        if max_entries < 1:
            raise ValidationError("max_entries must be at least 1")
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple[Any, ...], RiskScore] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def name(self) -> str:
        return f"cached({self._wrapped.name})"

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        key = (model_ref, tuple(sorted((k, float(v)) for k, v in features.items())))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self._hits += 1
                return cached
            self._misses += 1

        result = self._wrapped.score(features, model_ref=model_ref)

        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return result

    def stats(self) -> dict[str, int]:
        """Return cache hits, misses and current occupancy."""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "occupancy": len(self._cache),
            }


# ---------------------------------------------------------------------------
# 5. Resilience layer: graceful degradation
# ---------------------------------------------------------------------------


class FallbackDecorator(ScoringDecorator):
    """Falls back to a secondary component when the primary is unavailable.

    This is the operational answer to a real dependency risk: a hosted model
    endpoint will be unavailable at some point.  Failing every transaction
    when it is would be a self-inflicted denial of service, and silently
    allowing every transaction would remove the control entirely.  The
    decorator instead delegates to a deterministic secondary -- the rules
    engine -- and marks the substitution in the rationale so the degraded
    decision is visible in the audit trail rather than indistinguishable
    from a normal one.

    A degraded-decision counter is exposed so the substitution is
    observable: a control that silently stops working is worse than no
    control, because it is trusted.
    """

    def __init__(
        self,
        wrapped: ScoringComponent,
        fallback: ScoringComponent,
        on_degrade: Callable[[Exception], None] | None = None,
    ) -> None:
        super().__init__(wrapped)
        if not isinstance(fallback, ScoringComponent):
            raise ValidationError("fallback must be a ScoringComponent")
        if fallback is wrapped:
            raise ValidationError("fallback must not be the primary component")
        self._fallback = fallback
        self._on_degrade = on_degrade
        self._lock = threading.Lock()
        self._degraded = 0
        self._last_error: str | None = None

    @property
    def name(self) -> str:
        return f"fallback({self._wrapped.name} -> {self._fallback.name})"

    @property
    def fallback(self) -> ScoringComponent:
        """Expose the secondary component."""
        return self._fallback

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> RiskScore:
        try:
            return self._wrapped.score(features, model_ref=model_ref)
        except ProviderUnavailableError as exc:
            with self._lock:
                self._degraded += 1
                self._last_error = type(exc).__name__
            if self._on_degrade is not None:
                self._on_degrade(exc)
            degraded = self._fallback.score(features, model_ref=model_ref)
            # Rebuild rather than mutate: RiskScore is frozen, and the
            # substitution must be recorded in the immutable record.
            return RiskScore(
                score=degraded.score,
                decision=degraded.decision,
                rationale=f"[degraded: primary unavailable] {degraded.rationale}",
                strategy_name=degraded.strategy_name,
                model_version=degraded.model_version,
                contributing_features=dict(degraded.contributing_features),
            )

    def degradation_count(self) -> int:
        """Return how many requests have been served by the fallback."""
        with self._lock:
            return self._degraded


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def compose_scoring_pipeline(
    strategy: RiskScoringStrategy,
    *,
    policy: DecisionPolicy | None = None,
    fallback_strategy: RiskScoringStrategy | None = None,
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], float] | None = None,
    cache_entries: int | None = 256,
    extra_sensitive_keys: Sequence[str] = (),
) -> ScoringComponent:
    """Assemble a scoring pipeline in a defensible default order.

    Order, outermost first:

    1. ``RedactingDecorator`` -- no inner layer, local or remote, ever sees
       an unredacted identifier.
    2. ``MetricsDecorator`` -- measures the whole chain below it, so the
       recorded latency is the latency the caller experiences, including
       any fallback.
    3. ``AuditingDecorator`` -- records the decision the caller actually
       receives, including a degraded one.  It therefore sits *outside* the
       fallback: an auditor placed inside it would never see the substituted
       decision, and the audit trail would omit precisely the decisions an
       operator most needs to review.
    4. ``FallbackDecorator`` -- absorbs provider failure before it reaches
       the caller.
    5. ``CachingDecorator`` -- innermost, so a cache hit skips the expensive
       model call but still passes through every governance layer.
    6. ``StrategyComponent`` -- the algorithm itself.

    Each layer is optional, which is the point: the same core is assembled
    differently for a test (no cache, no audit sink), for local development
    (rules only) and for production (full stack).
    """
    core: ScoringComponent = StrategyComponent(strategy, policy=policy)

    if cache_entries:
        core = CachingDecorator(core, max_entries=cache_entries)
    if fallback_strategy is not None:
        core = FallbackDecorator(
            core, StrategyComponent(fallback_strategy, policy=policy)
        )
    # Auditing wraps the fallback, not the other way round: a degraded
    # decision must reach the audit trail rather than die inside the layer
    # that produced it.
    core = AuditingDecorator(core, sink=audit_sink)
    core = MetricsDecorator(core, clock=clock)
    core = RedactingDecorator(core, extra_sensitive_keys=extra_sensitive_keys)
    return core


__all__ = [
    "AuditingDecorator",
    "CachingDecorator",
    "FallbackDecorator",
    "MetricsDecorator",
    "RedactingDecorator",
    "ScoringComponent",
    "ScoringDecorator",
    "StrategyComponent",
    "compose_scoring_pipeline",
]
