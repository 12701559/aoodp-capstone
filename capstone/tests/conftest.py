"""Shared fixtures, stubs and test doubles.

Design note for the critical commentary
--------------------------------------
Everything the engine depends on that would otherwise touch the outside
world -- an HTTP transport, a clock, an audit sink, a model backend -- is a
constructor argument, so each one can be replaced here by a deterministic
double.  The consequence worth stating plainly: **the entire suite runs
offline, with no API keys, no network access and no cost, and still
exercises the remote-model code path end to end.**  That is the practical
payoff of the dependency inversion in the design, not a testing trick.

The doubles fall into three named categories, and the names matter because
they describe what each one can and cannot prove:

* **stub** -- returns canned data, used to drive a code path
  (:class:`CannedTransport`, :class:`FailingBackend`,
  :class:`StubScoringStrategy`, :class:`StubScoringComponent`);
* **fake** -- a working lightweight implementation
  (:class:`RecordingBackend`);
* **spy** -- records the calls it receives so a contract can be asserted
  (:class:`CallRecorder`, and the ``calls`` lists carried by the stubs).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

import pytest

from capstone.decorators import ScoringComponent
from capstone.domain import RiskScore, Transaction
from capstone.feature_store import FeatureContext
from capstone.strategies import DecisionPolicy, RiskScoringStrategy, StrategyOutput

# A fixed, timezone-aware moment.  Every test that asserts on the odd-hour
# feature uses this rather than ``datetime.now()`` so the suite cannot fail
# because it was run at a different time of day.
FIXED_TS = datetime(2026, 3, 4, 2, 30, tzinfo=timezone.utc)
DAY_TS = datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class CallRecorder:
    """A spy that records every call it receives."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return None

    @property
    def count(self) -> int:
        return len(self.calls)

    def single_request(self) -> Mapping[str, Any]:
        """Return the single request payload, asserting exactly one call."""
        assert len(self.calls) == 1, f"expected one call, saw {len(self.calls)}"
        (request,), _ = self.calls[0]
        return request


class CannedTransport:
    """A stub transport returning a fixed payload, or raising a fixed error."""

    def __init__(
        self,
        payload: Mapping[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.requests: list[Mapping[str, Any]] = []
        self._payload = dict(payload) if payload is not None else {"score": 0.42}
        self._error = error

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.requests.append(dict(request))
        if self._error is not None:
            raise self._error
        return self._payload


class RecordingBackend:
    """A fake scoring backend: deterministic, and it records its inputs."""

    def __init__(self, score: float = 0.5, rationale: str = "fake backend") -> None:
        self.seen: list[dict[str, Any]] = []
        self._score = score
        self._rationale = rationale

    def predict(
        self, features: Mapping[str, float], model_ref: str
    ) -> Mapping[str, Any]:
        self.seen.append({"features": dict(features), "model_ref": model_ref})
        return {
            "score": self._score,
            "contributions": {"amount_zscore": 0.2},
            "explanation": self._rationale,
        }


class FailingBackend:
    """A backend that always fails, simulating a provider outage."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or TimeoutError("simulated provider timeout")
        self.attempts = 0

    def predict(
        self, features: Mapping[str, float], model_ref: str
    ) -> Mapping[str, Any]:
        self.attempts += 1
        raise self._error


class SequenceClock:
    """A deterministic clock returning a scripted sequence of values."""

    def __init__(self, values: Sequence[float] | None = None) -> None:
        self._values = list(values or [0.0, 0.0, 1.0, 1.0])
        self._index = 0

    def __call__(self) -> float:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


class StubScoringComponent(ScoringComponent):
    """A minimal ``ScoringComponent`` used to isolate each decorator.

    Subclasses the real contract rather than merely imitating it, so a
    decorator's ``isinstance`` guard is exercised honestly and the stub
    cannot silently drift away from the interface it stands in for.  It
    records every feature mapping it receives, which is how the redaction
    tests prove what the inner layer actually saw.
    """

    def __init__(
        self,
        output: StrategyOutput,
        name: str = "stub-component",
        error: Exception | None = None,
    ) -> None:
        self._output = output
        self._name = name
        self._error = error
        self.calls: list[Mapping[str, float]] = []
        self.model_refs: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def score(self, features: Mapping[str, float], model_ref: str = "n/a") -> RiskScore:
        self.calls.append(dict(features))
        self.model_refs.append(model_ref)
        if self._error is not None:
            raise self._error
        value = self._output.normalised_score
        return RiskScore(
            score=value,
            decision=DecisionPolicy().decide(value),
            rationale=self._output.rationale,
            strategy_name=self._name,
            model_version=model_ref,
            contributing_features=dict(self._output.contributions),
        )


class StubScoringStrategy(RiskScoringStrategy):
    """A stub *strategy* used to control the innermost behaviour.

    ``StrategyComponent`` refuses anything that is not a real
    ``RiskScoringStrategy``, so a decorator test that needs to script the
    algorithm cannot substitute a component.  This double satisfies the ABC
    honestly instead of being mocked out, which means the type guard is
    exercised for real and the double cannot drift from the contract it
    stands in for.
    """

    def __init__(
        self,
        output: StrategyOutput | None = None,
        name: str = "stub-strategy",
        error: Exception | None = None,
    ) -> None:
        self._output = output
        self._name = name
        self._error = error
        self.calls: list[Mapping[str, float]] = []

    @property
    def name(self) -> str:
        return self._name

    def score(
        self, features: Mapping[str, float], model_ref: str = "n/a"
    ) -> StrategyOutput:
        self.calls.append(dict(features))
        if self._error is not None:
            raise self._error
        output = self._output or StrategyOutput(
            normalised_score=0.5, rationale="stub strategy", contributions={}
        )
        return StrategyOutput(
            normalised_score=output.normalised_score,
            rationale=output.rationale,
            contributions=dict(output.contributions),
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_transaction() -> Callable[..., Transaction]:
    """Return a factory producing valid transactions with sensible defaults."""

    def _make(
        amount: float = 1250.00,
        account_number: str = "ACC-00000001",
        channel: str = "wire",
        merchant_category: str = "crypto_exchange",
        country: str = "RU",
        timestamp: datetime = FIXED_TS,
        txn_id: str = "txn-fixed-0001",
    ) -> Transaction:
        return Transaction(
            account_number=account_number,
            amount=amount,
            channel=channel,
            merchant_category=merchant_category,
            country=country,
            timestamp=timestamp,
            txn_id=txn_id,
        )

    return _make


@pytest.fixture()
def high_risk_transaction(make_transaction: Callable[..., Transaction]) -> Transaction:
    """A transaction designed to fire most rules: foreign, crypto, wire, 02:30."""
    return make_transaction()


@pytest.fixture()
def low_risk_transaction(make_transaction: Callable[..., Transaction]) -> Transaction:
    """A transaction designed to fire no rule: domestic, groceries, branch, 14:30."""
    return make_transaction(
        amount=8.50,
        channel="branch",
        merchant_category="groceries",
        country="GB",
        timestamp=DAY_TS,
        txn_id="txn-fixed-0002",
    )


@pytest.fixture()
def context() -> FeatureContext:
    """A deterministic account context used across the suite."""
    return FeatureContext(
        home_country="GB",
        account_mean_amount=100.0,
        account_stddev_amount=50.0,
        recent_txn_count=14,
        velocity_window_hours=1,
    )


@pytest.fixture()
def quiet_context() -> FeatureContext:
    """A low-activity domestic context."""
    return FeatureContext(
        home_country="GB",
        account_mean_amount=100.0,
        account_stddev_amount=50.0,
        recent_txn_count=1,
        velocity_window_hours=4,
    )
