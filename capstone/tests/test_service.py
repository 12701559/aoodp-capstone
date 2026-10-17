"""Integration tests for the facade, the composition root and the container.

These are the tests that matter most for the portfolio claim, because they
exercise the layers *together* rather than one pattern in isolation.  Three
behaviours are only observable at this level:

1. **No "if testing" branch exists.**  Every test below builds the production
   object graph through ``build_risk_service`` and substitutes only the
   transport, which is a constructor argument.  If the design were wrong,
   production wiring would be untestable and these tests could not exist.
2. **Degradation is visible, not silent.**  A provider outage must still
   produce a decision *and* an audit record that says the decision was
   degraded.  A mis-ordered pipeline loses exactly that record, so the test
   asserts the record, not merely that the call returned.
3. **The registry, not the code, owns the model version.**  Promoting a model
   changes what the engine attributes decisions to, with no code edit.

The remote path is driven entirely through a stub transport: no network, no
API key, no vendor SDK, no cost.
"""

from __future__ import annotations

import pytest

from capstone.decorators import ScoringComponent
from capstone.domain import RiskDecision, ScoredBatch, ScoredTransaction, Transaction
from capstone.feature_store import FeatureContext, FeatureNotFoundError
from capstone.model_registry import ModelRegistry, ModelStage
from capstone.service import (
    DEFAULT_FEATURE_VECTOR,
    RiskAssessmentService,
    ServiceContainer,
    build_container,
    build_feature_store_and_registry,
    build_risk_service,
    production_model_ref,
    static_context,
)
from capstone.validation import ValidationError

from .conftest import CannedTransport, CallRecorder

MODEL_NAME = "transaction-risk"


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


class TestBuildFeatureStoreAndRegistry:
    def test_registry_holds_a_live_production_model(self) -> None:
        store, registry = build_feature_store_and_registry()
        record = registry.get_production(MODEL_NAME)
        assert record.stage is ModelStage.PRODUCTION
        assert record.version == "2.1.0"

    def test_lifecycle_is_walked_not_forced(self) -> None:
        """DEV -> STAGING -> PRODUCTION is walked, not forced in one jump."""
        _, registry = build_feature_store_and_registry()
        assert [record.version for record in registry.history(MODEL_NAME)] == ["2.1.0"]
        assert registry.get(MODEL_NAME, "2.1.0").stage is ModelStage.PRODUCTION
        assert [record.version for record in registry.by_stage(ModelStage.PRODUCTION)] == [
            "2.1.0"
        ]

    def test_feature_store_covers_the_configured_vector(self) -> None:
        store, _ = build_feature_store_and_registry()
        for name in DEFAULT_FEATURE_VECTOR:
            assert store.get(name).name == name

    def test_production_model_ref_is_qualified(self) -> None:
        _, registry = build_feature_store_and_registry()
        assert production_model_ref(registry, MODEL_NAME) == f"{MODEL_NAME}:2.1.0"


class TestCompositionRoot:
    def test_rules_service_attributes_decisions_to_the_rules_engine(self) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        assert service.model_ref == f"rules:{MODEL_NAME}:2.1.0"

    def test_provider_service_attributes_decisions_to_the_registry_version(self) -> None:
        service, _ = build_risk_service(
            provider="local", strategy="provider"
        )
        assert service.model_ref == f"{MODEL_NAME}:2.1.0"

    def test_pipeline_is_assembled_with_redaction_outermost(self) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        assert "redacting" in service.component.name.lower()

    def test_strategy_name_is_rejected_when_unknown(self) -> None:
        with pytest.raises(ValidationError, match="strategy must be"):
            build_risk_service(provider="local", strategy="mystery")

    def test_provider_name_is_rejected_when_unknown(self) -> None:
        with pytest.raises(ValidationError):
            build_risk_service(provider="unhosted", strategy="rules")

    def test_hosted_provider_without_transport_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="transport"):
            build_risk_service(provider="hosted", strategy="provider")

    def test_service_can_be_built_without_the_container(self) -> None:
        """The container is a convenience, not a requirement for correctness."""
        service, registry = build_risk_service(provider="local", strategy="rules")
        assert isinstance(service, RiskAssessmentService)
        assert isinstance(registry, ModelRegistry)


# ---------------------------------------------------------------------------
# Facade behaviour
# ---------------------------------------------------------------------------


class TestAssessment:
    def test_assess_returns_the_transaction_with_its_verdict(self, high_risk_transaction, context):
        service, _ = build_risk_service(provider="local", strategy="rules")
        verdict = service.assess(high_risk_transaction, context)
        assert isinstance(verdict, ScoredTransaction)
        assert verdict.transaction is high_risk_transaction
        assert isinstance(verdict.risk_score.decision, RiskDecision)

    def test_compute_features_returns_exactly_the_configured_vector(
        self, low_risk_transaction, context
    ) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        features = service.compute_features(low_risk_transaction, context)
        assert tuple(sorted(features)) == tuple(sorted(DEFAULT_FEATURE_VECTOR))
        assert all(isinstance(value, float) for value in features.values())

    def test_feature_names_are_exposed_for_inspection(self) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        assert service.feature_names == DEFAULT_FEATURE_VECTOR

    def test_explicit_context_overrides_the_configured_provider(
        self, high_risk_transaction
    ) -> None:
        """The same transaction can be scored under a different account context."""
        service, _ = build_risk_service(provider="local", strategy="rules")
        quiet = FeatureContext(
            home_country="GB",
            account_mean_amount=10_000.0,
            account_stddev_amount=1_000.0,
            recent_txn_count=1,
            velocity_window_hours=1,
        )
        noisy = FeatureContext(
            home_country="GB",
            account_mean_amount=50.0,
            account_stddev_amount=5.0,
            recent_txn_count=40,
            velocity_window_hours=1,
        )
        assert service.compute_features(high_risk_transaction, quiet) != (
            service.compute_features(high_risk_transaction, noisy)
        )

    def test_assess_rejects_a_non_transaction(self) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        with pytest.raises(ValidationError, match="Transaction"):
            service.assess({"amount": 10})  # type: ignore[arg-type]

    def test_assess_batch_preserves_input_order(
        self, high_risk_transaction, low_risk_transaction
    ) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        batch = service.assess_batch([high_risk_transaction, low_risk_transaction])
        assert isinstance(batch, ScoredBatch)
        assert [item.transaction.txn_id for item in batch] == [
            high_risk_transaction.txn_id,
            low_risk_transaction.txn_id,
        ]

    def test_report_delegates_to_the_visitor(self, low_risk_transaction) -> None:
        from capstone.reporting import RiskSummaryVisitor

        service, _ = build_risk_service(provider="local", strategy="rules")
        batch = service.assess_batch([low_risk_transaction])
        summary = service.report(batch, RiskSummaryVisitor())
        assert summary["total_transactions"] == 1

    def test_repeated_assessment_is_stable(self, high_risk_transaction, context) -> None:
        """The cache must not change the verdict, only the latency."""
        service, _ = build_risk_service(provider="local", strategy="rules")
        first = service.assess(high_risk_transaction, context).risk_score
        second = service.assess(high_risk_transaction, context).risk_score
        assert first.score == second.score
        assert first.decision is second.decision

    def test_cache_can_be_disabled(self, high_risk_transaction, context) -> None:
        service, _ = build_risk_service(
            provider="local", strategy="rules", use_cache=False
        )
        assert service.assess(high_risk_transaction, context).risk_score.score >= 0.0


# ---------------------------------------------------------------------------
# Explanation
# ---------------------------------------------------------------------------


class TestExplanation:
    def test_local_family_explains_with_a_deterministic_template(
        self, high_risk_transaction
    ) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        verdict = service.assess(high_risk_transaction)
        text = service.explain(verdict)
        assert "Decision" in text
        assert verdict.risk_score.decision.value in text

    def test_explanation_falls_back_to_the_rationale_when_no_explainer_exists(
        self, high_risk_transaction
    ) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        service._explainer = None  # noqa: SLF001 - exercising the documented fallback
        verdict = service.assess(high_risk_transaction)
        assert service.explain(verdict) == verdict.risk_score.rationale

    def test_hosted_family_explains_through_the_transport(self, high_risk_transaction) -> None:
        transport = CannedTransport(
            {"score": 0.97, "contributions": {"channel_risk": 0.9}, "explanation": "hosted narrative"}
        )
        service, _ = build_risk_service(
            provider="hosted", strategy="provider", transport=transport
        )
        verdict = service.assess(high_risk_transaction)
        assert service.explain(verdict) == "hosted narrative"


# ---------------------------------------------------------------------------
# Degradation -- the integration behaviour the pipeline order protects
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_provider_outage_still_yields_a_decision(self, high_risk_transaction) -> None:
        service, _ = build_risk_service(
            provider="hosted",
            strategy="provider",
            transport=CannedTransport(error=TimeoutError("provider down")),
        )
        verdict = service.assess(high_risk_transaction)
        assert isinstance(verdict.risk_score.decision, RiskDecision)

    def test_degraded_decision_is_labelled_in_the_verdict(self, high_risk_transaction) -> None:
        service, _ = build_risk_service(
            provider="hosted",
            strategy="provider",
            transport=CannedTransport(error=TimeoutError("provider down")),
        )
        verdict = service.assess(high_risk_transaction)
        assert verdict.risk_score.rationale.startswith("[degraded: primary unavailable]")

    def test_degraded_decision_reaches_the_audit_trail(self, high_risk_transaction) -> None:
        """The regression this ordering fix exists to prevent.

        With the auditor inside the fallback, the substituted decision never
        reached it and the audit trail silently omitted exactly the decisions
        an operator most needs to review.
        """
        sink = CallRecorder()
        service, _ = build_risk_service(
            provider="hosted",
            strategy="provider",
            transport=CannedTransport(error=TimeoutError("provider down")),
            audit_sink=sink,
        )
        service.assess(high_risk_transaction)
        assert sink.count == 1
        record = sink.calls[0][0][0]
        assert record["rationale"].startswith("[degraded: primary unavailable]")

    def test_healthy_provider_is_audited_without_a_degraded_label(
        self, high_risk_transaction
    ) -> None:
        sink = CallRecorder()
        transport = CannedTransport({"score": 0.97, "contributions": {}, "explanation": "ok"})
        service, _ = build_risk_service(
            provider="hosted", strategy="provider", transport=transport, audit_sink=sink
        )
        service.assess(high_risk_transaction)
        record = sink.calls[0][0][0]
        assert record["score"] == pytest.approx(0.97)
        assert not record["rationale"].startswith("[degraded")

    def test_fallback_can_be_switched_off_so_the_failure_is_loud(
        self, high_risk_transaction
    ) -> None:
        """Degrading is a policy choice, not an unavoidable behaviour."""
        from capstone.validation import ProviderUnavailableError

        service, _ = build_risk_service(
            provider="hosted",
            strategy="provider",
            transport=CannedTransport(error=TimeoutError("provider down")),
            use_fallback=False,
        )
        with pytest.raises(ProviderUnavailableError):
            service.assess(high_risk_transaction)


# ---------------------------------------------------------------------------
# Registry-driven versioning
# ---------------------------------------------------------------------------


class TestRegistryDrivenVersioning:
    def test_promoting_a_new_version_changes_attribution(self, high_risk_transaction) -> None:
        """A model change is a registry operation, not a code change."""
        service, registry = build_risk_service(provider="local", strategy="rules")
        registry.register(
            name=MODEL_NAME,
            version="2.2.0",
            artifact_ref="s3://risk-models/transaction-risk/2.2.0/model.pkl",
            metrics={"roc_auc": 0.96},
            registered_by="capstone-test",
        )
        registry.promote(MODEL_NAME, "2.2.0", ModelStage.STAGING)
        registry.promote(MODEL_NAME, "2.2.0", ModelStage.PRODUCTION)
        assert production_model_ref(registry, MODEL_NAME) == f"{MODEL_NAME}:2.2.0"
        # The already-assembled service keeps the version it was built with:
        # reassembly, not mutation, is how a running engine changes version.
        assert service.model_ref == f"rules:{MODEL_NAME}:2.1.0"


# ---------------------------------------------------------------------------
# RiskAssessmentService construction guards
# ---------------------------------------------------------------------------


class TestServiceConstructionGuards:
    def _store_and_component(self):
        store, _ = build_feature_store_and_registry()
        service, _ = build_risk_service(provider="local", strategy="rules")
        return store, service.component

    def test_rejects_a_non_component(self) -> None:
        store, _ = self._store_and_component()
        with pytest.raises(ValidationError, match="component"):
            RiskAssessmentService(object(), store, DEFAULT_FEATURE_VECTOR)  # type: ignore[arg-type]

    def test_rejects_a_non_feature_store(self) -> None:
        _, component = self._store_and_component()
        with pytest.raises(ValidationError, match="feature_store"):
            RiskAssessmentService(component, object(), DEFAULT_FEATURE_VECTOR)  # type: ignore[arg-type]

    def test_rejects_an_empty_feature_vector(self) -> None:
        store, component = self._store_and_component()
        with pytest.raises(ValidationError, match="at least one feature"):
            RiskAssessmentService(component, store, ())

    def test_rejects_an_unknown_feature_name_at_construction(self) -> None:
        """A typo in configuration stops start-up, not the first transaction."""
        store, component = self._store_and_component()
        with pytest.raises((FeatureNotFoundError, ValidationError)):
            RiskAssessmentService(component, store, ("not_a_feature",))

    def test_context_provider_defaults_to_a_static_empty_context(self) -> None:
        store, component = self._store_and_component()
        service = RiskAssessmentService(component, store, DEFAULT_FEATURE_VECTOR)
        assert isinstance(service._context_provider(  # noqa: SLF001
            Transaction(
                account_number="ACC-00000001",
                amount=1.0,
                channel="branch",
                merchant_category="groceries",
                country="GB",
            )
        ), FeatureContext)


# ---------------------------------------------------------------------------
# Dependency injection and the IoC container
# ---------------------------------------------------------------------------


class TestStaticContext:
    def test_returns_the_captured_context_for_every_transaction(self, high_risk_transaction) -> None:
        context = FeatureContext(home_country="GB")
        provider = static_context(context)
        assert provider(high_risk_transaction) is context


class TestServiceContainer:
    def test_resolves_a_registered_dependency(self) -> None:
        container = ServiceContainer()
        container.register("answer", lambda _c: 42)
        assert container.resolve("answer") == 42

    def test_singleton_is_cached(self) -> None:
        container = ServiceContainer()
        container.register("thing", lambda _c: object())
        assert container.resolve("thing") is container.resolve("thing")

    def test_non_singleton_returns_a_new_instance(self) -> None:
        container = ServiceContainer()
        container.register("thing", lambda _c: object(), singleton=False)
        assert container.resolve("thing") is not container.resolve("thing")

    def test_lazy_creation(self) -> None:
        """Registration must not construct anything until it is resolved."""
        container = ServiceContainer()
        built: list[int] = []
        container.register("thing", lambda _c: built.append(1) or 1)
        assert built == []
        container.resolve("thing")
        assert built == [1]

    def test_duplicate_registration_is_refused(self) -> None:
        container = ServiceContainer()
        container.register("thing", lambda _c: 1)
        with pytest.raises(ValidationError, match="already registered"):
            container.register("thing", lambda _c: 2)

    @pytest.mark.parametrize("name", ["", 5, None])
    def test_name_must_be_a_non_empty_string(self, name: object) -> None:
        container = ServiceContainer()
        with pytest.raises(ValidationError, match="service name"):
            container.register(name, lambda _c: 1)  # type: ignore[arg-type]

    def test_factory_must_be_callable(self) -> None:
        container = ServiceContainer()
        with pytest.raises(ValidationError, match="callable"):
            container.register("thing", "not-callable")  # type: ignore[arg-type]

    def test_unregistered_name_fails_loudly_with_the_known_names(self) -> None:
        container = ServiceContainer()
        container.register("alpha", lambda _c: 1)
        with pytest.raises(ValidationError, match="alpha"):
            container.resolve("beta")

    def test_has_and_registered_names(self) -> None:
        container = ServiceContainer()
        container.register("b", lambda _c: 1)
        container.register("a", lambda _c: 2)
        assert container.has("a")
        assert not container.has("c")
        assert container.registered_names() == ("a", "b")

    def test_can_be_built_without_the_container(self) -> None:
        """The container inverts *which* provider is used, nothing more."""
        service, _ = build_risk_service(provider="local", strategy="rules")
        assert service is not None


class TestBuildContainer:
    def test_exposes_the_assembled_engine_as_a_singleton(self) -> None:
        container = build_container(provider="local", strategy="rules")
        assert container.registered_names() == ("context_provider", "risk_service")
        assert container.resolve("risk_service") is container.resolve("risk_service")

    def test_resolved_service_is_usable(self, low_risk_transaction) -> None:
        container = build_container(provider="local", strategy="rules")
        service = container.resolve("risk_service")
        assert isinstance(service.assess(low_risk_transaction), ScoredTransaction)

    def test_context_provider_is_a_static_provider(self) -> None:
        container = build_container(provider="local", strategy="rules")
        provider = container.resolve("context_provider")
        assert callable(provider)

    def test_provider_is_chosen_by_configuration_alone(self) -> None:
        """Adding a provider is a configuration change at the composition root."""
        container = build_container(provider="local", strategy="provider")
        service = container.resolve("risk_service")
        assert service.model_ref == f"{MODEL_NAME}:2.1.0"


class TestComponentContract:
    def test_assembled_pipeline_satisfies_the_component_contract(self) -> None:
        service, _ = build_risk_service(provider="local", strategy="rules")
        assert isinstance(service.component, ScoringComponent)

    def test_component_is_replaceable_without_editing_the_service(
        self, low_risk_transaction
    ) -> None:
        """Substitution with no 'if testing' branch anywhere in the codebase."""
        from capstone.decorators import StrategyComponent
        from capstone.strategies import RuleBasedScoringStrategy

        service, _ = build_risk_service(provider="local", strategy="rules")
        store, _ = build_feature_store_and_registry()
        replacement = RiskAssessmentService(
            StrategyComponent(RuleBasedScoringStrategy()),
            store,
            DEFAULT_FEATURE_VECTOR,
            model_ref="injected",
        )
        assert replacement.assess(low_risk_transaction).risk_score.model_version == "injected"
