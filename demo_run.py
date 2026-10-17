#!/usr/bin/env python3
"""End-to-end demonstration of the AI-ready risk-assessment engine.

This script is the capstone's *runtime* evidence: it exercises the assembled
engine the way a production caller would, and it prints what each design
decision produces at run time rather than asserting it in prose.

It is entirely offline and deterministic.  No model API is called, no API
key is read, and no network socket is opened: the hosted-provider path is
driven through an injected transport, which is the same seam the test suite
uses.  Every number printed below is reproducible from this file alone.

Sections
    1. Composition root -- what the assembled pipeline actually looks like.
    2. Batch assessment -- the rules engine over six varied transactions.
    3. Reporting visitors -- four reports over one assessed batch.
    4. Audit trail -- the governance record written for every decision.
    5. Privacy boundary -- redaction before data crosses the process edge.
    6. Resilience -- provider outage, retry budget and graceful degradation.
    7. Model registry -- lifecycle, promotion and versioned attribution.
    8. Inversion of control -- container-managed dependencies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from capstone.decorators import (
    AuditingDecorator,
    FallbackDecorator,
    MetricsDecorator,
    RedactingDecorator,
    ScoringComponent,
    StrategyComponent,
    compose_scoring_pipeline,
)
from capstone.domain import ScoredBatch, Transaction
from capstone.feature_store import FeatureContext
from capstone.model_registry import ModelLifecycleError, ModelStage
from capstone.providers import HostedLlmProviderFactory
from capstone.reporting import (
    BlockedTransactionVisitor,
    ChannelRiskVisitor,
    JsonExportVisitor,
    RiskSummaryVisitor,
)
from capstone.service import (
    DEFAULT_FEATURE_VECTOR,
    RiskAssessmentService,
    build_container,
    build_feature_store_and_registry,
    build_risk_service,
    production_model_ref,
)
from capstone.strategies import ModelBackedScoringStrategy, RuleBasedScoringStrategy
from capstone.validation import ValidationError, redact_mapping

DAY = datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc)
NIGHT = datetime(2026, 3, 4, 2, 30, tzinfo=timezone.utc)
EVENING = datetime(2026, 3, 4, 23, 30, tzinfo=timezone.utc)

RULE_OF = "=" * 78


def rule(title: str) -> None:
    print(f"\n{RULE_OF}\n{title}\n{RULE_OF}")


def build_demo_transactions() -> list[tuple[Transaction, FeatureContext, str]]:
    """Return (transaction, context, label) triples covering the decision range."""
    return [
        (
            Transaction(
                account_number="GB12-3456-7890-4321",
                amount=85.00,
                channel="pos",
                merchant_category="groceries",
                country="GB",
                timestamp=DAY,
                txn_id="TX-1001",
            ),
            FeatureContext(),
            "routine domestic card payment",
        ),
        (
            Transaction(
                account_number="GB12-3456-7890-9876",
                amount=2600.00,
                channel="online",
                merchant_category="crypto_exchange",
                country="GB",
                timestamp=DAY,
                txn_id="TX-1002",
                ),
            FeatureContext(),
            "large online crypto purchase",
        ),
        (
            Transaction(
                account_number="GB12-3456-7890-1234",
                amount=4200.00,
                channel="wire",
                merchant_category="crypto_exchange",
                country="NG",
                timestamp=NIGHT,
                txn_id="TX-1003",
            ),
            # The account has moved 42 times in the last hour: the velocity
            # feature is context, not a property of this transaction alone.
            FeatureContext(recent_txn_count=42),
            "night-time foreign wire in a transaction burst",
        ),
        (
            Transaction(
                account_number="GB12-3456-7890-5555",
                amount=900.00,
                channel="atm",
                merchant_category="electronics",
                country="GB",
                timestamp=DAY,
                txn_id="TX-1004",
            ),
            FeatureContext(),
            "high-value cash-machine withdrawal",
        ),
        (
            Transaction(
                account_number="GB12-3456-7890-7777",
                amount=150.00,
                channel="mobile",
                merchant_category="utilities",
                country="GB",
                timestamp=DAY,
                txn_id="TX-1005",
            ),
            FeatureContext(),
            "regular utility bill",
        ),
        (
            Transaction(
                account_number="GB12-3456-7890-8888",
                amount=350.00,
                channel="online",
                merchant_category="money_transfer",
                country="DE",
                timestamp=EVENING,
                txn_id="TX-1006",
            ),
            FeatureContext(),
            "cross-border money transfer",
        ),
    ]


def section_1_composition_root() -> object:
    rule("1. COMPOSITION ROOT -- the assembled pipeline")

    audit_records: list[dict[str, object]] = []
    service, registry = build_risk_service(
        provider="local",
        strategy="rules",
        audit_sink=audit_records.append,
    )

    print(f"provider          : local heuristic (no off-process egress)")
    print(f"strategy          : rules-v1 (deterministic, fully explainable)")
    print(f"feature vector    : {', '.join(service.feature_names)}")
    print(f"model_ref         : {service.model_ref}")
    print(f"pipeline (outermost first):")
    print(f"    {service.component.name}")

    print("\nDefault decision policy: score < 0.45 ALLOW, < 0.80 REVIEW, else BLOCK")
    return service, registry, audit_records


def section_2_batch(service: RiskAssessmentService) -> ScoredBatch:
    rule("2. BATCH ASSESSMENT -- rules engine over six transactions")

    cases = build_demo_transactions()
    scored = []
    header = f"{'txn':8} {'amount':>9} {'channel':<7} {'country':<8} " \
             f"{'score':>6} {'decision':<7} strategy"
    print(header)
    print("-" * len(header))
    for transaction, context, label in cases:
        element = service.assess(transaction, context)
        score = element.risk_score
        scored.append(element)
        print(
            f"{transaction.txn_id:8} {transaction.amount:9.2f} "
            f"{transaction.channel:<7} {transaction.country:<8} "
            f"{score.score:6.2f} {score.decision.value:<7} {score.strategy_name}"
        )

    print("\nFeature vectors and fired rules (the audit trail of the number):")
    for (transaction, context, label), element in zip(cases, scored):
        features = service.compute_features(transaction, context)
        rendered = " ".join(f"{k}={v:g}" for k, v in features.items())
        print(f"\n  {transaction.txn_id} -- {label}")
        print(f"    features : {rendered}")
        print(f"    rationale: {element.risk_score.rationale}")

    print("\nDecision explanation for the highest-risk element:")
    print(f"    {service.explain(scored[2])}")

    return ScoredBatch(items=tuple(scored))


def section_3_visitors(service: RiskAssessmentService, batch: ScoredBatch) -> None:
    rule("3. REPORTING VISITORS -- four reports, one unchanged batch")

    print("3a. RiskSummaryVisitor (aggregate policy view)")
    summary = service.report(batch, RiskSummaryVisitor())
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))

    print("\n3b. BlockedTransactionVisitor(minimum_score=0.80) -- review queue")
    blocked = service.report(batch, BlockedTransactionVisitor(minimum_score=0.80))
    print(json.dumps(blocked, indent=2, sort_keys=True, default=str))

    print("\n3c. ChannelRiskVisitor -- mean risk per channel")
    channels = service.report(batch, ChannelRiskVisitor())
    print(json.dumps(channels, indent=2, sort_keys=True, default=str))

    print("\n3d. JsonExportVisitor -- machine-readable export (account suffix only)")
    export = service.report(batch, JsonExportVisitor(indent=2))
    document = json.loads(export)
    print(f"    export size: {len(export)} characters, "
          f"{len(document['transactions'])} records")
    print("    first record:")
    print(json.dumps(document["transactions"][0], indent=6, sort_keys=True))
    full_numbers = [
        record
        for record in document["transactions"]
        if "account_number" in record
    ]
    print(f"    records containing a full account number: {len(full_numbers)}")


def section_4_audit_trail(audit_records: list[dict[str, object]]) -> None:
    rule("4. AUDIT TRAIL -- one governance record per decision")

    print(f"records written: {len(audit_records)}")
    print("fields per record:", ", ".join(sorted(audit_records[0])))
    print("\nevery record (verbatim):")
    for record in audit_records:
        print("  " + json.dumps(record, sort_keys=True, default=str))

    identifiers = {
        key
        for record in audit_records
        for key in record
        if "account" in key.lower() or "customer" in key.lower()
    }
    print(f"\naccount/customer identifiers present in the audit trail: "
          f"{sorted(identifiers) or 'none'}")


def section_5_privacy_boundary() -> None:
    rule("5. PRIVACY BOUNDARY -- redaction before data leaves the process")

    sensitive = {
        "log_amount": 5.2,
        "amount_zscore": 2.9,
        "velocity": 4.0,
        "account_number": "GB33BUKB20201555555555",
        "customer_email": "ada.lovelace@example.com",
        "txn_id": "TX-2001",
    }
    masked = redact_mapping(sensitive)

    print("redact_mapping() -- keys matching a sensitive marker are masked:")
    for key in sensitive:
        print(f"    {key:15} {sensitive[key]!r:38} -> {masked[key]!r}")

    print("\nsame boundary observed on the wire: a hosted provider reached")
    print("through a recording transport, fed a payload carrying identifiers.")

    outbound: list[dict[str, object]] = []

    def recording_transport(request: dict) -> dict:
        outbound.append(json.loads(json.dumps(request)))
        return {
            "score": 0.58,
            "contributions": {"amount_zscore": 0.21, "velocity": 0.08},
            "explanation": "model: moderate risk driven by amount deviation",
        }

    factory = HostedLlmProviderFactory(recording_transport)
    config, provider_strategy, _explainer = factory.assemble()
    pipeline = RedactingDecorator(
        StrategyComponent(ModelBackedScoringStrategy(provider_strategy.backend)),
    )
    result = pipeline.score(dict(sensitive), model_ref=config.model_ref)

    print(f"    provider config : {config.provider_name}, endpoint={config.endpoint}")
    print(f"    redact_before_send={config.redact_before_send}, "
          f"max_retries={config.max_retries}")
    print(f"    decision        : score={result.score} "
          f"decision={result.decision.value}")
    print("    request that reached the transport:")
    print(json.dumps(outbound[0]["features"], indent=6, sort_keys=True))
    leaked = [
        key
        for key, value in outbound[0]["features"].items()
        if isinstance(value, str) and value.startswith("GB33")
    ]
    print(f"    unmasked identifiers observed by the transport: {leaked or 'none'}")


def section_6_resilience() -> None:
    rule("6. RESILIENCE -- provider outage, retry budget, degradation")

    attempts: list[dict[str, object]] = []

    def failing_transport(request: dict) -> dict:
        attempts.append(dict(request))
        raise TimeoutError("upstream model gateway did not answer")

    factory = HostedLlmProviderFactory(failing_transport, max_retries=2)
    config, provider_strategy, _explainer = factory.assemble()
    primary = StrategyComponent(ModelBackedScoringStrategy(provider_strategy.backend))

    degraded_events: list[str] = []
    fallback = FallbackDecorator(
        primary,
        StrategyComponent(RuleBasedScoringStrategy()),
        on_degrade=lambda exc: degraded_events.append(type(exc).__name__),
    )
    pipeline = RedactingDecorator(
        MetricsDecorator(AuditingDecorator(fallback, sink=lambda _r: None))
    )

    transaction, context, _label = build_demo_transactions()[2]
    store, _registry = build_feature_store_and_registry()
    features = store.compute_vector(DEFAULT_FEATURE_VECTOR, transaction, context)

    result = pipeline.score(features, model_ref=config.model_ref)

    print("6a. Primary provider fails; the caller still receives a decision")
    print(f"    transport attempts      : {len(attempts)} "
          f"(1 initial + {config.max_retries} retries)")
    print(f"    on_degrade callbacks    : {degraded_events}")
    print(f"    degradations recorded   : {fallback.degradation_count()}")
    print(f"    decision returned       : score={result.score} "
          f"decision={result.decision.value}")
    print(f"    rationale               : {result.rationale}")

    print("\n6b. Same outage through the real composition root (build_risk_service)")
    service, _reg = build_risk_service(
        provider="hosted",
        strategy="provider",
        transport=failing_transport,
        use_fallback=True,
    )
    element = service.assess(transaction, context)
    print(f"    model_ref : {service.model_ref}")
    print(f"    decision  : score={element.risk_score.score} "
          f"decision={element.risk_score.decision.value}")
    print(f"    rationale : {element.risk_score.rationale}")
    print(f"    degraded  : "
          f"{element.risk_score.rationale.startswith('[degraded')}")
    print(f"    total transport attempts across both runs: {len(attempts)}")

    print("\n6c. A misbehaving provider is rejected rather than trusted")

    def malformed_transport(request: dict) -> dict:
        return {"confidence": 0.99, "verdict": "looks fine to me"}

    service_broken, _reg = build_risk_service(
        provider="hosted",
        strategy="provider",
        transport=malformed_transport,
        use_fallback=True,
    )
    broken = service_broken.assess(transaction, context)
    print(f"    payload      : {{\"confidence\": 0.99, \"verdict\": \"looks fine\"}}")
    print(f"    decision     : score={broken.risk_score.score} "
          f"decision={broken.risk_score.decision.value}")
    print(f"    rationale    : {broken.risk_score.rationale}")


def section_7_registry(registry) -> None:
    rule("7. MODEL REGISTRY -- lifecycle, promotion, versioned attribution")

    print(f"live production model at start-up: "
          f"{production_model_ref(registry, 'transaction-risk')}")

    record = registry.register(
        name="transaction-risk",
        version="2.2.0",
        artifact_ref="s3://risk-models/transaction-risk/2.2.0/model.pkl",
        metrics={"roc_auc": 0.961, "precision_at_10": 0.78, "reference_psi": 0.02},
        registered_by="risk-science",
    )
    print(f"\nregistered {record.qualified_name} in stage {record.stage.value}")

    staging = registry.promote("transaction-risk", "2.2.0", ModelStage.STAGING)
    print(f"promoted to {staging.stage.value}")

    production = registry.promote("transaction-risk", "2.2.0", ModelStage.PRODUCTION)
    print(f"promoted to {production.stage.value}")

    print("\nversion history:")
    for item in registry.history("transaction-risk"):
        metric = item.metric("roc_auc")
        print(f"    {item.qualified_name:26} {item.stage.value:<11} "
              f"roc_auc={metric:.3f} registered_by={item.registered_by}")

    print(f"\nproduction_model_ref() now returns: "
          f"{production_model_ref(registry, 'transaction-risk')}")
    print("    -> the swap was a registry operation, not a code change.")

    archived_transitions = [
        (version, stage)
        for version in ("2.1.0",)
        for stage in (ModelStage.PRODUCTION,)
    ]
    for version, stage in archived_transitions:
        try:
            registry.promote("transaction-risk", version, stage)
            print(f"    promoting archived {version} -> {stage.value}: ALLOWED")
        except ModelLifecycleError as exc:
            print(f"    promoting archived {version} -> {stage.value}: refused")
            print(f"        {exc}")

    print("\na decision taken after promotion records the new version:")
    store, _unused = build_feature_store_and_registry()
    component: ScoringComponent = compose_scoring_pipeline(
        RuleBasedScoringStrategy(), audit_sink=None, cache_entries=None
    )
    service = RiskAssessmentService(
        component=component,
        feature_store=store,
        feature_names=DEFAULT_FEATURE_VECTOR,
        model_ref=production_model_ref(registry, "transaction-risk"),
    )
    transaction, context, _label = build_demo_transactions()[2]
    element = service.assess(transaction, context)
    print(f"    model_version on the decision: {element.risk_score.model_version}")
    print(f"    model_ref on the service     : {service.model_ref}")


def section_8_container() -> None:
    rule("8. INVERSION OF CONTROL -- container-managed dependencies")

    container = build_container(provider="local", strategy="rules", use_cache=False)
    print(f"registered services: {', '.join(container.registered_names())}")

    first = container.resolve("risk_service")
    second = container.resolve("risk_service")
    print(f"singleton risk_service reused: {first is second}")
    print(f"resolved pipeline: {first.component.name}")
    print(f"resolved model_ref: {first.model_ref}")

    try:
        container.resolve("fraud_model_endpoint")
    except ValidationError as exc:
        print(f"unregistered dependency requested -> {type(exc).__name__}")
        print(f"    {exc}")


def main() -> int:
    print("AI-READY RISK-ASSESSMENT ENGINE -- CAPSTONE DEMONSTRATION")
    print("Offline and deterministic: no API keys, no sockets, no vendor SDKs.")

    service, registry, audit_records = section_1_composition_root()
    batch = section_2_batch(service)
    section_3_visitors(service, batch)
    section_4_audit_trail(audit_records)
    section_5_privacy_boundary()
    section_6_resilience()
    section_7_registry(registry)
    section_8_container()

    rule("DEMONSTRATION COMPLETE")
    print("Every section above ran against the same shipped package that the")
    print("test suite exercises; no network access and no model credentials")
    print("were required, and no external service was contacted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
