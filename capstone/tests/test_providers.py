"""Unit tests for the provider families (Abstract Factory) and the AI paths.

Why these tests exist
---------------------
The whole remote-model path is exercised here **without a network, without a
vendor SDK and without an API key**, because the transport is a constructor
dependency rather than an import.  That is the claim the portfolio makes about
dependency inversion; these tests are the evidence for it.

The tests are grouped by the question each group answers:

1. Is configuration self-defending?  (``ProviderConfig``)
2. Does an explanation stay honest about its inputs?  (generators)
3. Does the remote path fail loudly and boundedly?  (``HostedLlmBackend``)
4. Can an unsafe provider family be assembled at all?  (factory invariants)
5. Does name-based resolution reject what it cannot build?
"""

from __future__ import annotations

import pytest

from capstone.domain import RiskDecision, RiskScore
from capstone.providers import (
    AVAILABLE_PROVIDERS,
    AiProviderFactory,
    ExplanationGenerator,
    HostedLlmBackend,
    HostedLlmProviderFactory,
    LlmNarrativeExplanationGenerator,
    LocalHeuristicBackend,
    LocalHeuristicProviderFactory,
    ProviderConfig,
    TemplateExplanationGenerator,
    build_provider_factory,
)
from capstone.strategies import ModelBackedScoringStrategy
from capstone.validation import ProviderUnavailableError, ValidationError

from .conftest import CannedTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_score(
    value: float = 0.91,
    contributions: dict[str, float] | None = None,
    decision: RiskDecision = RiskDecision.BLOCK,
) -> RiskScore:
    """Return a valid ``RiskScore`` with controllable contributions."""
    return RiskScore(
        score=value,
        decision=decision,
        rationale="synthetic score for provider tests",
        strategy_name="test-strategy",
        model_version="test-model:1.0.0",
        contributing_features=contributions or {},
    )


def local_config(**overrides: object) -> ProviderConfig:
    """Return a minimal valid on-premise configuration."""
    base: dict[str, object] = {
        "provider_name": "local",
        "model_ref": "heuristic-local:1.0.0",
        "endpoint": None,
        "timeout_seconds": 1.0,
        "max_retries": 0,
        "redact_before_send": False,
        "supports_narrative_explanation": True,
    }
    base.update(overrides)
    return ProviderConfig(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. ProviderConfig
# ---------------------------------------------------------------------------


class TestProviderConfig:
    """Configuration is immutable and refuses unsafe or nonsensical values."""

    def test_valid_local_config_is_accepted(self) -> None:
        config = local_config()
        assert config.endpoint is None
        assert config.redact_before_send is False

    @pytest.mark.parametrize(
        "endpoint",
        ["http://models.example-bank.internal/v1", "models.example-bank.internal"],
    )
    def test_endpoint_must_use_https(self, endpoint: str) -> None:
        """Plaintext transport of derived financial features is refused."""
        with pytest.raises(ValidationError, match="https"):
            local_config(endpoint=endpoint)

    def test_https_endpoint_is_accepted(self) -> None:
        config = local_config(endpoint="https://models.example-bank.internal/v1")
        assert config.endpoint.startswith("https://")

    @pytest.mark.parametrize("timeout", [0.0, -1.0, 120.1])
    def test_timeout_is_bounded_and_positive(self, timeout: float) -> None:
        with pytest.raises(ValidationError):
            local_config(timeout_seconds=timeout)

    @pytest.mark.parametrize("retries", [-1, 6])
    def test_max_retries_is_bounded(self, retries: int) -> None:
        with pytest.raises(ValidationError, match="max_retries"):
            local_config(max_retries=retries)

    def test_max_retries_rejects_boolean(self) -> None:
        """``True`` is an ``int`` in Python; the type check must not accept it."""
        with pytest.raises(ValidationError, match="max_retries"):
            local_config(max_retries=True)

    @pytest.mark.parametrize("flag", ["yes", 1, None])
    def test_flags_must_be_booleans(self, flag: object) -> None:
        with pytest.raises(ValidationError, match="boolean"):
            local_config(redact_before_send=flag)

    def test_config_is_frozen(self) -> None:
        """A running engine must not have its redaction policy mutated."""
        config = local_config()
        with pytest.raises(Exception):
            config.redact_before_send = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. Explanation generators
# ---------------------------------------------------------------------------


class TestTemplateExplanationGenerator:
    """The deterministic explainer: same input, same sentence, always."""

    def test_names_the_top_contributing_factors(self) -> None:
        explainer = TemplateExplanationGenerator()
        score = make_score(
            contributions={"amount_zscore": 0.40, "channel_risk": 0.90, "is_foreign": 0.55}
        )
        text = explainer.explain({}, score)
        assert "channel_risk" in text
        assert "amount_zscore" in text
        assert score.decision.value in text

    def test_limits_to_three_drivers(self) -> None:
        explainer = TemplateExplanationGenerator()
        score = make_score(
            contributions={
                "alpha": 0.1,
                "bravo": 0.2,
                "charlie": 0.3,
                "delta": 0.4,
                "echo": 0.5,
            }
        )
        text = explainer.explain({}, score)
        assert "echo" in text and "delta" in text and "charlie" in text
        assert "bravo" not in text and "alpha" not in text

    def test_reports_absence_of_risk_factors(self) -> None:
        explainer = TemplateExplanationGenerator()
        text = explainer.explain({}, make_score(value=0.1, contributions={}))
        assert "no risk factor exceeded its threshold" in text

    def test_ignores_non_positive_contributions(self) -> None:
        """A protective factor must not be presented as a risk driver."""
        explainer = TemplateExplanationGenerator()
        score = make_score(contributions={"amount_zscore": -0.5})
        assert "no risk factor exceeded its threshold" in explainer.explain({}, score)

    def test_is_deterministic(self) -> None:
        explainer = TemplateExplanationGenerator()
        score = make_score(contributions={"channel_risk": 0.9})
        assert explainer.explain({}, score) == explainer.explain({}, score)


class TestLlmNarrativeExplanationGenerator:
    """The hosted explainer, tested through a stub transport."""

    def test_returns_stripped_narrative(self) -> None:
        transport = CannedTransport({"explanation": "  Elevated risk due to channel.  "})
        explainer = LlmNarrativeExplanationGenerator(transport, "hosted-llm:2.1.0")
        assert explainer.explain({}, make_score()) == "Elevated risk due to channel."

    def test_sends_contributions_and_not_raw_features(self) -> None:
        """Data minimisation asserted at the egress boundary."""
        transport = CannedTransport({"explanation": "ok"})
        explainer = LlmNarrativeExplanationGenerator(transport, "hosted-llm:2.1.0")
        explainer.explain({"log_amount": 7.1, "is_foreign": 1.0}, make_score())
        request = transport.requests[0]
        assert "features" not in request
        assert request["contributions"] == {}
        assert request["task"] == "explain_risk_decision"

    def test_transport_failure_becomes_provider_error(self) -> None:
        transport = CannedTransport(error=TimeoutError("socket closed"))
        explainer = LlmNarrativeExplanationGenerator(transport, "hosted-llm:2.1.0")
        with pytest.raises(ProviderUnavailableError, match="explanation provider failed"):
            explainer.explain({}, make_score())

    @pytest.mark.parametrize("payload", [{}, {"explanation": ""}, {"explanation": "   "}])
    def test_empty_narrative_is_rejected(self, payload: dict[str, object]) -> None:
        explainer = LlmNarrativeExplanationGenerator(CannedTransport(payload), "m:1")
        with pytest.raises(ProviderUnavailableError, match="no text"):
            explainer.explain({}, make_score())

    def test_non_mapping_response_is_rejected(self) -> None:
        explainer = LlmNarrativeExplanationGenerator(lambda _r: ["not", "a", "mapping"], "m:1")
        with pytest.raises(ProviderUnavailableError, match="no text"):
            explainer.explain({}, make_score())

    def test_requires_callable_transport(self) -> None:
        with pytest.raises(ValidationError, match="transport"):
            LlmNarrativeExplanationGenerator("not-callable", "m:1")  # type: ignore[arg-type]

    def test_is_an_explanation_generator(self) -> None:
        explainer = LlmNarrativeExplanationGenerator(CannedTransport(), "m:1")
        assert isinstance(explainer, ExplanationGenerator)


# ---------------------------------------------------------------------------
# 3. Backends
# ---------------------------------------------------------------------------


class TestLocalHeuristicBackend:
    """The on-premise model: same port, no egress."""

    def test_predict_returns_the_agreed_payload_shape(self) -> None:
        payload = LocalHeuristicBackend().predict(
            {"amount_zscore": 1.0, "channel_risk": 1.0}, model_ref="heuristic-local:1.0.0"
        )
        assert set(payload) == {"score", "contributions", "explanation"}
        assert 0.0 <= float(payload["score"]) <= 1.0
        assert isinstance(payload["contributions"], dict)
        assert "heuristic-local:1.0.0" in str(payload["explanation"])

    def test_predict_is_deterministic(self) -> None:
        backend = LocalHeuristicBackend()
        features = {"amount_zscore": 0.5, "is_foreign": 1.0}
        first = backend.predict(features, model_ref="m")
        second = backend.predict(features, model_ref="m")
        assert first == second

    def test_satisfies_the_backend_port(self) -> None:
        """The strategy's runtime protocol check must accept it."""
        from capstone.strategies import ScoringBackend

        assert isinstance(LocalHeuristicBackend(), ScoringBackend)


class TestHostedLlmBackend:
    """The remote model: retries, bounds, and never trusts the reply."""

    def test_success_on_first_attempt(self) -> None:
        transport = CannedTransport({"score": 0.77})
        backend = HostedLlmBackend(transport, max_retries=2)
        assert backend.predict({"a": 1.0}, "hosted-llm:2.1.0")["score"] == 0.77
        assert backend.attempts == 1

    def test_retries_then_succeeds(self) -> None:
        calls: list[int] = []

        def flaky(_request: object) -> dict[str, float]:
            calls.append(1)
            if len(calls) < 3:
                raise TimeoutError("transient")
            return {"score": 0.66}

        backend = HostedLlmBackend(flaky, max_retries=2)
        assert backend.predict({"a": 1.0}, "m")["score"] == 0.66
        assert backend.attempts == 3

    def test_exhausted_retries_raise_with_attempt_count(self) -> None:
        backend = HostedLlmBackend(CannedTransport(error=TimeoutError("down")), max_retries=2)
        with pytest.raises(ProviderUnavailableError, match=r"after 3 attempt\(s\)"):
            backend.predict({"a": 1.0}, "m")
        assert backend.attempts == 3

    def test_zero_retries_means_one_attempt(self) -> None:
        backend = HostedLlmBackend(CannedTransport(error=TimeoutError("down")), max_retries=0)
        with pytest.raises(ProviderUnavailableError):
            backend.predict({}, "m")
        assert backend.attempts == 1

    @pytest.mark.parametrize("payload", [{}, {"result": "not a score"}])
    def test_unusable_payload_is_retried_then_fails(self, payload: dict[str, object]) -> None:
        """A misbehaving provider must not be accepted just because it replied."""
        transport = CannedTransport(payload)  # type: ignore[arg-type]
        backend = HostedLlmBackend(transport, max_retries=1)
        with pytest.raises(ProviderUnavailableError):
            backend.predict({}, "m")
        assert backend.attempts == 2

    def test_requires_callable_transport(self) -> None:
        with pytest.raises(ValidationError, match="transport"):
            HostedLlmBackend(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("retries", [-1, 6])
    def test_requires_bounded_retries(self, retries: int) -> None:
        with pytest.raises(ValidationError, match="max_retries"):
            HostedLlmBackend(CannedTransport(), max_retries=retries)


# ---------------------------------------------------------------------------
# 4. Family consistency invariants
# ---------------------------------------------------------------------------


class _InsecureEgressFamily(AiProviderFactory):
    """A deliberately unsafe family: it sends data off-process unredacted."""

    def create_config(self) -> ProviderConfig:
        return local_config(
            provider_name="insecure",
            endpoint="https://models.example-bank.internal/v1",
            redact_before_send=False,
        )

    def create_backend(self) -> LocalHeuristicBackend:
        return LocalHeuristicBackend()

    def create_explanation_generator(self) -> ExplanationGenerator:
        return TemplateExplanationGenerator()


class _NarrativeWithoutGeneratorFamily(AiProviderFactory):
    """A family that advertises a capability it does not supply."""

    def create_config(self) -> ProviderConfig:
        return local_config(supports_narrative_explanation=True)

    def create_backend(self) -> LocalHeuristicBackend:
        return LocalHeuristicBackend()

    def create_explanation_generator(self) -> None:  # type: ignore[override]
        return None


class TestFamilyConsistencyInvariants:
    """The invariants are enforced in code, so they cannot be forgotten."""

    def test_off_process_family_without_redaction_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="redact_before_send=True"):
            _InsecureEgressFamily().assemble()

    def test_narrative_claim_without_generator_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="no generator"):
            _NarrativeWithoutGeneratorFamily().assemble()

    def test_abstract_members_cannot_be_left_unimplemented(self) -> None:
        with pytest.raises(TypeError):
            AiProviderFactory()  # type: ignore[abstract]

    def test_local_family_may_skip_redaction_because_it_has_no_endpoint(self) -> None:
        """The rule is conditional on egress, not a blanket switch."""
        config, _, _ = LocalHeuristicProviderFactory().assemble()
        assert config.endpoint is None
        assert config.redact_before_send is False


# ---------------------------------------------------------------------------
# 5. Concrete factories and name-based resolution
# ---------------------------------------------------------------------------


class TestLocalHeuristicProviderFactory:
    def test_assemble_returns_a_coherent_family(self) -> None:
        config, strategy, explainer = LocalHeuristicProviderFactory().assemble()
        assert config.provider_name == "local"
        assert isinstance(strategy, ModelBackedScoringStrategy)
        assert isinstance(strategy.backend, LocalHeuristicBackend)
        assert isinstance(explainer, TemplateExplanationGenerator)

    def test_create_strategy_binds_the_local_backend(self) -> None:
        """The strategy depends on the port, never on a concrete class."""
        strategy = LocalHeuristicProviderFactory().create_strategy()
        assert isinstance(strategy.backend, LocalHeuristicBackend)

    def test_model_ref_is_configurable(self) -> None:
        config = LocalHeuristicProviderFactory(model_ref="heuristic-local:9.9.9").create_config()
        assert config.model_ref == "heuristic-local:9.9.9"


class TestHostedLlmProviderFactory:
    def test_assemble_returns_a_coherent_family(self) -> None:
        factory = HostedLlmProviderFactory(CannedTransport({"score": 0.5}))
        config, strategy, explainer = factory.assemble()
        assert config.provider_name == "hosted"
        assert config.redact_before_send is True
        assert isinstance(strategy.backend, HostedLlmBackend)
        assert isinstance(explainer, LlmNarrativeExplanationGenerator)

    def test_default_endpoint_is_https(self) -> None:
        config = HostedLlmProviderFactory(CannedTransport()).create_config()
        assert config.endpoint == "https://models.example-bank.internal/v1/score"

    def test_requires_callable_transport(self) -> None:
        with pytest.raises(ValidationError, match="transport"):
            HostedLlmProviderFactory(None)  # type: ignore[arg-type]

    def test_retry_budget_reaches_the_backend(self) -> None:
        factory = HostedLlmProviderFactory(
            CannedTransport(error=TimeoutError("down")), max_retries=0
        )
        backend = factory.create_backend()
        with pytest.raises(ProviderUnavailableError):
            backend.predict({}, "m")
        assert backend.attempts == 1


class TestBuildProviderFactory:
    def test_available_providers_are_declared(self) -> None:
        assert AVAILABLE_PROVIDERS == frozenset({"local", "hosted"})

    def test_local_resolution(self) -> None:
        assert isinstance(build_provider_factory("local"), LocalHeuristicProviderFactory)

    def test_hosted_resolution_requires_transport(self) -> None:
        with pytest.raises(ValidationError, match="requires a transport"):
            build_provider_factory("hosted")

    def test_hosted_resolution_with_transport(self) -> None:
        factory = build_provider_factory("hosted", transport=CannedTransport())
        assert isinstance(factory, HostedLlmProviderFactory)

    @pytest.mark.parametrize("name", ["", "aws-bedrock", "Hosted", "local "])
    def test_unknown_provider_name_is_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError):
            build_provider_factory(name, transport=CannedTransport())

    def test_kwargs_are_passed_through(self) -> None:
        factory = build_provider_factory("local", model_ref="custom:1.2.3")
        assert factory.create_config().model_ref == "custom:1.2.3"
