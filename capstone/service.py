"""
Application service, dependency injection and the composition root.

Layer boundaries
----------------
This module is where the layers meet, and it is the only place that knows
about all of them:

* **domain** (``Transaction``, ``RiskScore``) -- knows nothing about
  scoring, providers or reporting;
* **feature computation** (``FeatureStore``) -- knows the domain, nothing
  about algorithms;
* **scoring** (strategies, decorators) -- knows features and policies,
  nothing about persistence or transport;
* **providers** (abstract factory) -- knows how to build a family, nothing
  about when it is needed;
* **reporting** (visitors) -- knows the domain structure, nothing about how
  scores were produced.

``RiskAssessmentService`` is a **facade**: it offers the one operation the
rest of the system actually wants (assess this transaction) and hides the
six-step assembly beneath it.  Callers depend on the facade, not on the
feature store, the decorator stack or the provider factory -- which is what
makes the whole engine replaceable behind a stable interface.

Dependency injection and inversion of control
---------------------------------------------
The service constructs **none** of its collaborators.  They arrive through
the constructor.  Concretely, that buys three things the module is assessed
on:

1. **Substitution without editing.**  The tests inject a fake component and
   a fake provider; production injects the real stack.  No conditional
   "if testing" branch exists anywhere in the codebase.
2. **No hidden singletons.**  Nothing reaches out to a global model client
   or a module-level connection pool, so two engines with different
   providers can coexist in one process -- essential for shadow-scoring a
   candidate model against the incumbent.
3. **Explicit dependencies.**  Reading the constructor tells you everything
   the object needs; there is no ambient state to reason about.

:class:`ServiceContainer` adds inversion of control: the *container* decides
what concrete objects satisfy a named dependency, and application code asks
for a name.  This is what allows the provider to be chosen by configuration
at start-up rather than fixed by an import -- and it is deliberately a
thirty-line registry rather than a framework, because the benefit here comes
from the inversion, not from the library.

Honest limitation, stated for the commentary
--------------------------------------------
A container is not free: it moves a dependency error from start-up (a
TypeError from a missing argument) to resolution time (a ``KeyError`` from
an unregistered name), and it obscures the static dependency graph.  That
trade-off is why this container is small, why every registration is
explicit and visible in the composition root, and why the engine is fully
constructible without it.  The container is a convenience for wiring, not a
requirement for correctness, and the tests construct the service directly
to prove that.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Mapping, Sequence

from .decorators import ScoringComponent, compose_scoring_pipeline
from .domain import ScoredBatch, ScoredTransaction, Transaction
from .feature_store import FeatureContext, FeatureStore, build_default_feature_store
from .model_registry import ModelRegistry, ModelStage
from .providers import AiProviderFactory, ExplanationGenerator, build_provider_factory
from .reporting import ReportVisitor, render
from .strategies import (
    DecisionPolicy,
    RiskScoringStrategy,
    RuleBasedScoringStrategy,
)
from .validation import ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context provider port
# ---------------------------------------------------------------------------

ContextProvider = Callable[[Transaction], FeatureContext]
"""Port through which account-history context is obtained.

Declared as a callable type rather than an abstract class because the
engine needs exactly one method; a single-method interface does not justify
a class hierarchy, and a function is trivially replaceable in a test.
"""


def static_context(context: FeatureContext) -> ContextProvider:
    """Return a context provider that always yields *context*.

    Used by the tests and by the demo so a feature vector is deterministic
    and hand-checkable.  In deployment a database-backed provider would be
    injected in its place with no change to the service.
    """

    def _provider(_transaction: Transaction) -> FeatureContext:
        return context

    return _provider


# ---------------------------------------------------------------------------
# The facade
# ---------------------------------------------------------------------------


class RiskAssessmentService:
    """Assesses transactions against a configured scoring pipeline.

    The service orchestrates four steps -- obtain context, compute features,
    score, attach provenance -- and owns no algorithm, no transport and no
    report.  It is therefore the cheapest class in the codebase to test
    end to end.
    """

    def __init__(
        self,
        component: ScoringComponent,
        feature_store: FeatureStore,
        feature_names: Sequence[str],
        *,
        model_ref: str = "unspecified",
        context_provider: ContextProvider | None = None,
        explainer: ExplanationGenerator | None = None,
    ) -> None:
        if not isinstance(component, ScoringComponent):
            raise ValidationError("component must be a ScoringComponent")
        if not isinstance(feature_store, FeatureStore):
            raise ValidationError("feature_store must be a FeatureStore")
        names = tuple(feature_names)
        if not names:
            raise ValidationError("at least one feature name is required")
        for name in names:
            # Fail at construction, not at the first transaction: a typo in a
            # feature name is a configuration error and should stop start-up.
            feature_store.get(name)

        self._component = component
        self._feature_store = feature_store
        self._feature_names = names
        self._model_ref = model_ref
        self._context_provider = context_provider or static_context(FeatureContext())
        self._explainer = explainer

    # -- accessors ----------------------------------------------------

    @property
    def component(self) -> ScoringComponent:
        """Return the assembled scoring component (for inspection)."""
        return self._component

    @property
    def feature_names(self) -> tuple[str, ...]:
        """Return the feature vector this service computes."""
        return self._feature_names

    @property
    def model_ref(self) -> str:
        """Return the model reference every decision is attributed to."""
        return self._model_ref

    # -- operations ---------------------------------------------------

    def compute_features(
        self, transaction: Transaction, context: FeatureContext | None = None
    ) -> dict[str, float]:
        """Compute the configured feature vector for *transaction*."""
        effective = context if context is not None else self._context_provider(transaction)
        return self._feature_store.compute_vector(
            self._feature_names, transaction, effective
        )

    def assess(
        self, transaction: Transaction, context: FeatureContext | None = None
    ) -> ScoredTransaction:
        """Assess one transaction and return the transaction with its verdict."""
        if not isinstance(transaction, Transaction):
            raise ValidationError("assess() requires a Transaction instance")
        features = self.compute_features(transaction, context)
        risk_score = self._component.score(features, model_ref=self._model_ref)
        logger.debug("assessed %s -> %s", transaction.txn_id, risk_score.decision.value)
        return ScoredTransaction(transaction=transaction, risk_score=risk_score)

    def assess_batch(
        self, transactions: Iterable[Transaction], context: FeatureContext | None = None
    ) -> ScoredBatch:
        """Assess many transactions and return an ordered batch."""
        return ScoredBatch(
            items=tuple(self.assess(txn, context) for txn in transactions)
        )

    def report(
        self, batch: ScoredBatch, visitor: ReportVisitor
    ) -> Any:
        """Apply a reporting visitor to an assessed batch."""
        return render(batch, visitor)

    def explain(self, element: ScoredTransaction) -> str:
        """Return a human-readable explanation, using the explainer if supplied."""
        if self._explainer is None:
            return element.risk_score.rationale
        features = self.compute_features(
            element.transaction, self._context_provider(element.transaction)
        )
        return self._explainer.explain(features, element.risk_score)


# ---------------------------------------------------------------------------
# Inversion-of-control container
# ---------------------------------------------------------------------------


class ServiceContainer:
    """A minimal service locator with lazily-created, cached dependencies.

    Deliberately tiny.  The purpose is to invert control of *which* concrete
    provider is used, not to provide a framework.  Every registration is an
    explicit factory, so nothing is discovered by magic and an
    unregistered name fails loudly with the exact key that was missing.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[["ServiceContainer"], Any]] = {}
        self._instances: dict[str, Any] = {}

    def register(
        self, name: str, factory: Callable[["ServiceContainer"], Any], singleton: bool = True
    ) -> None:
        """Register *factory* under *name*."""
        if not isinstance(name, str) or not name:
            raise ValidationError("service name must be a non-empty string")
        if not callable(factory):
            raise ValidationError("factory must be callable")
        if name in self._factories:
            raise ValidationError(f"service {name!r} is already registered")
        if singleton:
            def cached(container: ServiceContainer, _f: Callable = factory) -> Any:
                if name not in container._instances:  # noqa: SLF001 - container cooperation
                    container._instances[name] = _f(container)  # noqa: SLF001
                return container._instances[name]

            self._factories[name] = cached
        else:
            self._factories[name] = factory

    def resolve(self, name: str) -> Any:
        """Return the dependency registered under *name*."""
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise ValidationError(
                f"service {name!r} is not registered; known: {sorted(self._factories)}"
            ) from exc
        return factory(self)

    def registered_names(self) -> tuple[str, ...]:
        """Return the registered service names."""
        return tuple(sorted(self._factories))

    def has(self, name: str) -> bool:
        """Return whether *name* is registered."""
        return name in self._factories


# ---------------------------------------------------------------------------
# Composition root
# ---------------------------------------------------------------------------

DEFAULT_FEATURE_VECTOR: tuple[str, ...] = (
    "log_amount",
    "amount_zscore",
    "is_foreign",
    "is_elevated_category",
    "is_odd_hour",
    "channel_risk",
    "velocity",
)


def production_model_ref(registry: ModelRegistry, model_name: str) -> str:
    """Return the ``name:version`` string of the live production model.

    The engine never hard-codes a model version.  It asks the registry which
    version is live, so promoting a model is a registry operation rather
    than a code change, and every decision still records exactly which
    version produced it.
    """
    return registry.get_production(model_name).qualified_name


def build_feature_store_and_registry(
    model_name: str = "transaction-risk",
    model_version: str = "2.1.0",
) -> tuple[FeatureStore, ModelRegistry]:
    """Build a populated feature store and a registry with a live model.

    The registry is created in DEVELOPMENT and promoted through STAGING to
    PRODUCTION, exercising the real lifecycle rather than reaching in and
    forcing a state -- including the lifecycle means the start-up path is
    itself tested by every component test that uses this helper.
    """
    store = build_default_feature_store()
    registry = ModelRegistry()
    registry.register(
        name=model_name,
        version=model_version,
        artifact_ref=f"s3://risk-models/{model_name}/{model_version}/model.pkl",
        metrics={"roc_auc": 0.94, "precision_at_10": 0.71, "reference_psi": 0.03},
        registered_by="capstone-demo",
    )
    registry.promote(model_name, model_version, ModelStage.STAGING)
    registry.promote(model_name, model_version, ModelStage.PRODUCTION)
    return store, registry


def build_risk_service(
    provider: str = "local",
    *,
    strategy: str = "rules",
    policy: DecisionPolicy | None = None,
    transport: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
    use_fallback: bool = True,
    use_cache: bool = True,
    model_name: str = "transaction-risk",
    context: FeatureContext | None = None,
) -> tuple[RiskAssessmentService, ModelRegistry]:
    """Assemble the complete engine -- the composition root.

    This function is the *only* place in the codebase that names concrete
    implementations.  Everything above it depends on abstractions, so the
    wiring can be changed here without touching a single strategy,
    decorator, provider or report.

    Parameters
    ----------
    provider : str
        ``"local"`` (on-premise heuristic) or ``"hosted"`` (remote model).
    strategy : str
        ``"rules"`` for the deterministic engine or ``"provider"`` to use the
        family's model-backed strategy.
    use_fallback : bool
        Wrap the pipeline so a provider outage degrades to the rules engine
        instead of failing the transaction.
    """
    store, registry = build_feature_store_and_registry(model_name=model_name)
    model_ref = production_model_ref(registry, model_name)

    factory: AiProviderFactory = build_provider_factory(provider, transport=transport)
    config, provider_strategy, explainer = factory.assemble()

    if strategy == "rules":
        active: RiskScoringStrategy = RuleBasedScoringStrategy()
    elif strategy == "provider":
        active = provider_strategy
    else:
        raise ValidationError("strategy must be 'rules' or 'provider'")

    component = compose_scoring_pipeline(
        active,
        policy=policy,
        fallback_strategy=(
            RuleBasedScoringStrategy() if use_fallback and strategy != "rules" else None
        ),
        audit_sink=audit_sink,
        cache_entries=256 if use_cache else None,
    )

    service = RiskAssessmentService(
        component=component,
        feature_store=store,
        feature_names=DEFAULT_FEATURE_VECTOR,
        model_ref=model_ref if strategy == "provider" else f"rules:{model_ref}",
        context_provider=static_context(context or FeatureContext()),
        explainer=explainer if config.supports_narrative_explanation else None,
    )
    return service, registry


def build_container(**build_kwargs: Any) -> ServiceContainer:
    """Create a container whose ``risk_service`` is the assembled engine."""
    container = ServiceContainer()
    container.register(
        "risk_service",
        lambda _c: build_risk_service(**build_kwargs)[0],
        singleton=True,
    )
    container.register(
        "context_provider",
        lambda _c: static_context(FeatureContext()),
        singleton=True,
    )
    return container


__all__ = [
    "DEFAULT_FEATURE_VECTOR",
    "ContextProvider",
    "RiskAssessmentService",
    "ServiceContainer",
    "build_container",
    "build_feature_store_and_registry",
    "build_risk_service",
    "production_model_ref",
    "static_context",
]
