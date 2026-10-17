"""
``capstone`` -- an AI-ready transaction risk-assessment engine.

This package is the integrating artefact of the e-Portfolio.  It is not a
collection of pattern exercises; it is one small system in which every
required pattern carries a real responsibility, and in which the patterns
interact in ways that can be demonstrated, tested and criticised.

Architecture at a glance
------------------------

::

    composition root  (service.build_risk_service)
            |
            |  builds everything, names every concrete class once
            v
    +---------------------+      +----------------------+
    |  ModelRegistry      |      |  AiProviderFactory   |  Abstract Factory
    |  (lifecycle/stage)  |      |  local | hosted      |
    +----------+----------+      +----------+-----------+
               |                            |
               |  production model ref      |  backend + config + explainer
               v                            v
    +---------------------------------------------------------------+
    |  ScoringComponent  (Strategy + Decorator chain)               |
    |                                                               |
    |  Redacting -> Metrics -> Fallback -> Auditing -> Caching      |
    |                       -> StrategyComponent -> Strategy        |
    |                                            |  rules          |
    |                                            |  weighted stats |
    |                                            |  model-backed   |
    +-------------------------------+-------------------------------+
                                    |
                                    v
    +---------------------------------------------------------------+
    |  FeatureStore  -- named, versioned, pure feature extractors   |
    +---------------------------------------------------------------+
                                    |
                                    v
    +---------------------------------------------------------------+
    |  ScoredBatch  --  Visitor pattern for reporting               |
    |  RiskSummary | BlockedList | JsonExport | ChannelBreakdown    |
    +---------------------------------------------------------------+

Design principles applied throughout
------------------------------------

* **Immutability by default.**  ``Transaction``, ``RiskScore``,
  ``FeatureDefinition``, ``ModelRecord`` and ``DecisionPolicy`` are frozen
  dataclasses.  A decision that has been taken cannot be edited after the
  fact, which is both a correctness property and an audit property.
* **Validation at the boundary.**  Every public constructor and function
  validates its input and raises a specific error type from
  :mod:`capstone.validation`.  Invalid states are unrepresentable rather
  than merely discouraged.
* **Dependency inversion at the seams.**  External models are reached
  through the ``ScoringBackend`` protocol and an injected transport callable.
  No module in this package imports an HTTP client or a vendor SDK, so the
  whole engine -- including its AI path -- runs offline and deterministically
  under test.
* **Observability of degradation.**  A fallback is a control that can stop
  working silently; the fallback decorator therefore counts and labels every
  degraded decision instead of hiding it.

Ethical posture encoded in the design
-------------------------------------

Three controls exist because of the ethical analysis in Tasks 2 and 3 of the
portfolio rather than because of a functional requirement:

1. :class:`~capstone.decorators.RedactingDecorator` is outermost, so no
   inner layer can observe an unredacted account identifier.
2. The hosted provider family *cannot be assembled* without
   ``redact_before_send=True``; the invariant is enforced by code, not
   documented in a comment.
3. Every :class:`~capstone.domain.RiskScore` carries the per-feature
   contributions that produced it, so an automated decision about a person
   can be explained without access to the model that made it.

Usage
-----

::

    from capstone import build_risk_service, RuleBasedScoringStrategy

    service, registry = build_risk_service(provider="local", strategy="rules")
    verdict = service.assess(transaction)
    print(verdict.risk_score.decision, verdict.risk_score.rationale)
"""

from __future__ import annotations

__version__ = "1.0.0"

from .decorators import (
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
from .domain import (
    ELEVATED_RISK_CATEGORIES,
    VALID_CHANNELS,
    VALID_MERCHANT_CATEGORIES,
    RiskDecision,
    RiskScore,
    ScoredBatch,
    ScoredTransaction,
    Transaction,
)
from .feature_store import (
    FeatureContext,
    FeatureDefinition,
    FeatureNotFoundError,
    FeatureStore,
    build_default_feature_store,
)
from .model_registry import ModelRecord, ModelRegistry, ModelStage
from .providers import (
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
from .reporting import (
    BlockedTransactionVisitor,
    ChannelRiskVisitor,
    JsonExportVisitor,
    ReportVisitor,
    RiskSummaryVisitor,
    render,
)
from .service import (
    DEFAULT_FEATURE_VECTOR,
    ContextProvider,
    RiskAssessmentService,
    ServiceContainer,
    build_container,
    build_feature_store_and_registry,
    build_risk_service,
    production_model_ref,
    static_context,
)
from .strategies import (
    DEFAULT_RULES,
    DecisionPolicy,
    ModelBackedScoringStrategy,
    RiskScoringStrategy,
    Rule,
    RuleBasedScoringStrategy,
    ScoringBackend,
    StrategyOutput,
    WeightedStatisticalStrategy,
)
from .validation import (
    FeatureStoreError,
    ModelLifecycleError,
    ModelNotFoundError,
    ProviderUnavailableError,
    RiskEngineError,
    ValidationError,
    redact_account_number,
    redact_mapping,
    validate_account_number,
    validate_amount,
    validate_choice,
    validate_country,
    validate_identifier,
    validate_text,
    validate_unit_interval,
    validate_weight_map,
)

__all__ = [
    "AVAILABLE_PROVIDERS",
    "DEFAULT_FEATURE_VECTOR",
    "DEFAULT_RULES",
    "ELEVATED_RISK_CATEGORIES",
    "VALID_CHANNELS",
    "VALID_MERCHANT_CATEGORIES",
    "AiProviderFactory",
    "AuditingDecorator",
    "BlockedTransactionVisitor",
    "CachingDecorator",
    "ChannelRiskVisitor",
    "ContextProvider",
    "DecisionPolicy",
    "ExplanationGenerator",
    "FallbackDecorator",
    "FeatureContext",
    "FeatureDefinition",
    "FeatureNotFoundError",
    "FeatureStore",
    "FeatureStoreError",
    "HostedLlmBackend",
    "HostedLlmProviderFactory",
    "JsonExportVisitor",
    "LlmNarrativeExplanationGenerator",
    "LocalHeuristicBackend",
    "LocalHeuristicProviderFactory",
    "MetricsDecorator",
    "ModelBackedScoringStrategy",
    "ModelLifecycleError",
    "ModelNotFoundError",
    "ModelRecord",
    "ModelRegistry",
    "ModelStage",
    "ProviderConfig",
    "ProviderUnavailableError",
    "RedactingDecorator",
    "ReportVisitor",
    "RiskAssessmentService",
    "RiskDecision",
    "RiskEngineError",
    "RiskScore",
    "RiskScoringStrategy",
    "Rule",
    "RuleBasedScoringStrategy",
    "ScoredBatch",
    "ScoredTransaction",
    "ScoringBackend",
    "ScoringComponent",
    "ScoringDecorator",
    "ServiceContainer",
    "StrategyComponent",
    "StrategyOutput",
    "TemplateExplanationGenerator",
    "Transaction",
    "ValidationError",
    "WeightedStatisticalStrategy",
    "__version__",
    "build_container",
    "build_default_feature_store",
    "build_feature_store_and_registry",
    "build_provider_factory",
    "build_risk_service",
    "compose_scoring_pipeline",
    "production_model_ref",
    "redact_account_number",
    "redact_mapping",
    "render",
    "static_context",
    "validate_account_number",
    "validate_amount",
    "validate_choice",
    "validate_country",
    "validate_identifier",
    "validate_text",
    "validate_unit_interval",
    "validate_weight_map",
]
