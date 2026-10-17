# e-Portfolio — Advanced Object-Oriented Programming

**Student:** 12701559 · **Module:** Advanced Object-Oriented Programming (Capstone e-Portfolio)
**Artefact repository:** `capstone/` — nine modules, 2,512 statements, 355 offline tests, 99% statement coverage
**Companion artefact (Unit 6):** `unit6/` thread-safe banking system, 39 tests
**Evidence:** `evidence/demo_run_output.txt`, `evidence/test_run_output.txt`, `evidence/coverage_report.txt`, `evidence/coverage_html/`

**Measured word count (Tasks 1–4, including section headings; excludes front matter, references and appendices):** Task 1 – 754 · Task 2 – 1,901 · Task 3 – 884 · Task 4 – 1,111 · **total 4,650**. References add 458 words and Appendices A–C add 552, so the whole file is 5,841 words. Per-section counts and a trim plan are in `word_count_and_trim_guide.md`.

> Tasks 1–4 are the assessed text; code is cited as artefact evidence rather than reproduced. Appendix A maps each claim to a file and capture, Appendix B is the collaboration record the student must supply, and Appendix C states the limits of the work. The brief states a 3,500-word equivalence, so this draft is roughly 30% over: read it as the substantive version, then trim to your tutor's enforcement using the plan in `word_count_and_trim_guide.md`.

---

## Task 1 — Summary of Key Learning Outcomes

### 1.1 Understand and implement secure coding practices

Security is structural here, not appended. **Construction-time validation:** `validation.py` centralises every check and domain error, and `Transaction` is a frozen slotted dataclass whose `__post_init__` calls those validators, so an invalid transaction cannot exist. Error messages never echo the rejected value, since reflecting untrusted input into logs or responses is the mechanism behind the injection class of defect (OWASP Foundation, 2021), and a timezone-naive timestamp is refused rather than assumed UTC. **Data minimisation at the boundary:** `RedactingDecorator` is outermost, so no inner layer — audit sink included — ever sees a raw identifier; `redact_mapping` returns a new dictionary rather than mutating the caller's; and `ProviderConfig` refuses a hosted family whose endpoint is not `https://` or whose `redact_before_send` is false. **Untrusted model replies:** `ModelBackedScoringStrategy` rejects a non-mapping reply and a missing, boolean, non-numeric or out-of-range score, and translates vendor exceptions into `ProviderUnavailableError` so no third-party detail leaks upward.

### 1.2 Apply advanced object-oriented principles to complex problems

The complexity is governance of an AI-assisted decision, not arithmetic. Five concerns normally entangled in one scoring script are separated — feature derivation (`feature_store.py`), algorithm (`strategies.py`), cross-cutting policy (`decorators.py`), lifecycle (`model_registry.py`), output format (`reporting.py`) — and joined only by the composition root in `service.py`. Encapsulation uses frozen value objects, polymorphism `RiskScoringStrategy` and `ReportVisitor`, abstraction the five-line `ScoringBackend` protocol, and inversion of control constructor injection of transport, clock, context provider, backend and sink. Algorithm, provider, model version and report format are therefore all replaceable without editing a consumer.

### 1.3 Utilise design patterns for reusable, maintainable, flexible code

| Pattern | Where | Flexibility it buys |
|---|---|---|
| Strategy | `strategies.py` | Rules, statistical or model-backed scoring swapped by one configuration string |
| Decorator | `decorators.py` | Redaction, metrics, audit, caching, fallback composed and reordered at one assembly point |
| Visitor | `reporting.py` | Reports added as classes; `ChannelRiskVisitor` was added last and touched no other file |
| Abstract Factory | `providers.py` | A whole provider family swapped together, with its consistency invariant enforced |
| Registry / Feature Store | `model_registry.py`, `feature_store.py` | Model version and feature definitions become runtime facts, not constants |

Each was chosen against a rejected alternative: Strategy over a scorer hierarchy; Decorator over subclass combinations; Visitor over report methods on the elements; Abstract Factory over a factory function, because *family* consistency is the real requirement; constructor injection over a framework container.

### 1.4 Design architectures suitable for large-scale systems, ensuring security and robustness

Each layer scales along the axis that matters: feature computation is pure and therefore cacheable and parallelisable; caching is an inner decorator, so it can become distributed without touching governance; the registry centralises lifecycle state so N scoring instances read one answer to "which version is live". Robustness is demonstrated, not asserted: a provider outage produces three transport attempts, an explicit `[degraded: primary unavailable]` rationale and a substituted decision that still reaches the audit trail; a hostile payload `{"confidence": 0.99, "verdict": "looks fine to me"}` is rejected; an illegal `archived -> production` promotion is refused.

### 1.5 Develop software adaptable for AI models and efficient for data science tasks

Three properties were designed in, not retrofitted. **Abstraction:** the AI layer is reached only through `ScoringBackend`, so replacing a hosted LLM with a local model, a second vendor or a fake is a composition-root change. **Contracts:** `StrategyOutput` carries `contributions` and `rationale`, making explainability part of the algorithm contract; `FeatureDefinition` is named, versioned and owned, so training and serving reference the same definitions. **Lifecycle:** the registry makes model version a runtime fact, the precondition for shadow deployment and rollback. Efficiency follows: pure extraction, an inner cache, and no vendor SDK outside `providers.py`.

### 1.6 Link to practical work

| Learning objective | Practical work |
|---|---|
| Secure coding | `validation.py`, `domain.py`, `RedactingDecorator`, `ProviderConfig`; run sections 5–6 |
| Advanced OOP for complex problems | Frozen slotted value objects, ABCs, a runtime-checkable protocol, injected dependencies |
| Design patterns | All four classic patterns in one cohesive engine, each with commentary in Task 2 |
| Large-scale architecture, robustness | `build_risk_service` composition root, documented pipeline ordering, degradation in run section 6 |
| AI adaptability | `model_registry.py`, `feature_store.py`; run section 7 changes the live version with no code change |

The Unit 6 artefact supplies the concurrency foundation, and the capstone reuses that discipline: `FeatureStore` takes a re-entrant lock specifically because `compute_vector` calls `get` internally, where a plain `Lock` would self-deadlock.

---

## Task 2 — Showcase of Artefacts

### 2.1 Artefact A — Thread-safe concurrent programming (Unit 6)

`bank_account.py` and `transaction_simulator.py` with 39 tests: an account mutated under a re-entrant lock, a transfer service, and a simulator driving concurrent transfers. **Principles:** encapsulated state behind methods, custom exception types for domain failure, and a re-entrant lock so a guarded method calling another guarded method cannot deadlock. **Challenge:** the simulator's first succeeded/failed figures did not reconcile with the accompanying screenshot — an inconsistency an assessor would notice, which taught me to reproduce evidence rather than remember it. Concurrency correctness cannot be argued, only demonstrated, so every lock in the capstone carries a test that would fail without it.

### 2.2 Artefact B — Strategy: interchangeable scoring algorithms

`RiskScoringStrategy` defines `score(features, model_ref) -> StrategyOutput`, implemented three ways: `RuleBasedScoringStrategy` (weighted rules), `WeightedStatisticalStrategy` (logistic scoring over a weight vector) and `ModelBackedScoringStrategy` (through the `ScoringBackend` protocol). `DecisionPolicy` maps a normalised score onto `ALLOW`/`REVIEW`/`BLOCK` at 0.45 and 0.80. A fraud engine must run an explainable rule set in one environment and a learned model in another without changing the pipeline, audit trail or reporting. `StrategyOutput` is uniform, so every consumer is written once, and `contributions` records which inputs drove the number, letting a declined customer be given a reason and an auditor reconstruct the decision without the model. **Critical commentary.** Strategy is correct here but not free: it forces every algorithm into `(features) → StrategyOutput`, so a future algorithm needing to *reject* a transaction outright would have to smuggle that through `contributions` or break the interface. I chose the narrower contract deliberately, because a richer return type would push policy into the algorithms. Holding the rule weights as data makes them inspectable by a non-programmer, but also means they can drift into unreviewed policy if nobody owns them.

### 2.3 Artefact C — Decorator: dynamic governance layers

`ScoringComponent` with a `name` property and a `score` method; `ScoringDecorator` holding the wrapped component; five layers — `RedactingDecorator`, `MetricsDecorator`, `AuditingDecorator`, `CachingDecorator`, `FallbackDecorator`; and `compose_scoring_pipeline()` as the single assembly point. The run shows what a client sees: `redacting(metered(audited(cached(rules-v1))))`. Each layer is one policy, testable in isolation, so no test needs the production chain to assert one behaviour. **Critical commentary — a defect found and fixed.** Assembly originally placed `AuditingDecorator` *inside* `FallbackDecorator`. Every per-decorator test passed; the defect appeared only when a test asserted that a degraded decision still reaches the audit sink — it did not, because the substituted decision was produced outside the auditor. The fix moves auditing outside the fallback, and the reasoning is now in the docstring: an auditor inside the fallback would never see the substituted decision, and the trail would omit precisely the decisions an operator most needs. This is the clearest evidence that testing complexity is a design signal — the tests were individually correct and collectively incomplete. Five decorators is close to the over-engineering line: I kept caching because a cache hit must still pass redaction and auditing, and dropped a proposed circuit breaker because `FallbackDecorator` already bounds outage damage and overlapping layers would make failure semantics ambiguous.

### 2.4 Artefact D — Visitor: analytics and reporting

`ReportVisitor` with `visit_batch`, `visit_scored_transaction` and `result`; four visitors — `RiskSummaryVisitor`, `BlockedTransactionVisitor(minimum_score)`, `JsonExportVisitor`, `ChannelRiskVisitor` — and a `render(batch, visitor)` driver. `ScoredBatch` and `ScoredTransaction` are immutable value objects that should not accumulate report logic, while reporting needs to add operations repeatedly. Adding a report is one class and no edit to the elements, demonstrated by `ChannelRiskVisitor`, and visitor state accumulates in the visitor, so no report interferes with another. **Critical commentary.** The known cost is that a new *element* type forces every visitor to change — acceptable because the element hierarchy is closed. The subtler risk is redaction drift: `JsonExportVisitor` exports only the last four characters of the account number, and the run asserts that no exported record contains a full identifier. Without that assertion the guarantee would rest on reviewers noticing an omission, and a report is a copy that travels further and is retained longer than its source.

### 2.5 Artefact E — Abstract Factory: swapping AI service providers

`AiProviderFactory` creates a *family*: `create_config()`, `create_backend()`, `create_explanation_generator()`. `LocalHeuristicProviderFactory` produces an on-premise heuristic backend and a template explainer; `HostedLlmProviderFactory` produces a networked backend and a narrative explainer, both around an **injected transport callable**. `assemble()` returns the family only after a consistency check. Config, client and explainer must be mutually consistent, and three inconsistencies are plausible — a hosted endpoint with redaction off, a local provider with an HTTPS endpoint, a narrative explainer paired with a backend that cannot produce one. **Critical commentary.** Abstract Factory is heavier than a factory function, and with two families it is arguable; I kept it because the family relationship is the real invariant and a tuple-returning function would make the consistency check optional and easy to skip. I deliberately added no plugin registry or entry-point discovery. The payoff is that nothing in `strategies.py` or `service.py` imports `requests`, `httpx` or a vendor SDK, so the remote path is exercised offline at no cost.

### 2.6 Artefacts F and G — Model Registry and Feature Store

**Registry** (`model_registry.py`). `ModelRecord` is immutable, carrying name, version, stage, artifact reference, metrics and `registered_by`; `ModelRegistry` is thread-safe and enforces `_ALLOWED_TRANSITIONS`. Run section 7 shows the lifecycle end to end: 2.1.0 is live; 2.2.0 is registered into `DEVELOPMENT`, promoted to `STAGING` then `PRODUCTION`, at which point 2.1.0 is archived; `production_model_ref()` then returns 2.2.0 and the next decision records `transaction-risk:2.2.0`. Promoting the archived 2.1.0 is refused with `illegal transition archived -> production`. **Defect found and fixed:** `promote()` returned the updated record but never wrote it back into the internal mapping. Single-call tests passed because the returned record was correct; the bug surfaced only when a test promoted a version and then asked the registry what was live. The lesson concerns test *shape*, not count — an assertion on a return value cannot detect a missing state mutation — so the registry now asserts through a second read path (`get_production`, `history`) after every transition.

**Feature Store** (`feature_store.py`). `FeatureDefinition` (name, version, description, `extractor`, `owner`), a store with a re-entrant lock, and seven default features — `log_amount`, `amount_zscore`, `is_foreign`, `is_elevated_category`, `is_odd_hour`, `channel_risk`, `velocity`. Re-registering the same name and version is a no-op so start-up is idempotent, while registering a *different* version under an existing name raises, forcing the caller to acknowledge that historical scores referenced the previous definition. **Critical commentary.** The decisive choice was refusing to treat a missing feature as zero and refusing a non-finite value silently: the rules engine skips a rule whose feature is absent, recording the omission by its absence from `contributions`, and `compute` converts a non-finite extractor result into `FeatureStoreError`. Both make failure attributable, which is what an auditor needs.

### 2.7 Artefact H — Dependency Injection and inversion of control

`RiskAssessmentService` takes its component, feature store, feature names, context provider and explainer as constructor arguments. `ServiceContainer` is a deliberately minimal locator with explicit factories, lazy singleton caching and a loud `ValidationError` naming the missing key; `build_risk_service()` is the composition root, the only function naming concrete implementations. Every dependency that would otherwise touch the outside world — transport, clock, audit sink, backend, context provider — is a constructor argument, which is what makes the suite deterministic and offline. **Critical commentary.** A framework container would be over-engineering at this size, so the container is about 45 lines and prefers explicit factories to convention-based resolution. The honest trade-off is that a service locator hides dependencies from the type checker, so it is used only at the outer edge, never inside a strategy or decorator (Fowler, 2004).

### 2.8 Artefact I — Testing: mocking and AI-assisted testing without live models

`capstone/tests/` — nine modules, 355 tests, 99% capstone coverage (2,512 statements, 23 uncovered) in under 1.5 seconds, entirely offline; 394 tests pass including Unit 6. `conftest.py` names its doubles by category: **stubs** (`CannedTransport`, `FailingBackend`, `StubScoringStrategy`, `StubScoringComponent`), a **fake** (`RecordingBackend`, a working lightweight implementation) and **spies** (`CallRecorder` with recorded `calls`/`requests`/`seen` lists). `CannedTransport` takes an optional payload *or* an optional error, so three lines cover success and outage; `RecordingBackend` records the features and model reference it receives so the call contract can be asserted. Three modules were written to close coverage gaps rather than raise a number, and two test defects were corrected for the right reason: one asserted a feature name was absent by searching for the letter "a", which failed because explanation text contains it, and one retry test passed a transport with no error, exercising the success path while claiming to test the retry budget.

**Reflection on AI-assisted testing.** I used an AI assistant as a test-authoring aid and reviewer. It added real value drafting tests from class contracts, discovering boundary and contract cases I had not enumerated (a boolean score, which is an `int` in Python and would otherwise pass `isinstance`; a payload missing the score key; a non-`ScoringComponent` passed to the service), and in regression support, since the degradation assertion now specifies the ordering decision. It also created risk: confidently wrong assertions — the "letter a" case, and a redaction expectation of one masked character when the implementation masks all but the last four — and a contract that did not exist, that `history()` would return an empty list for an unknown model when it deliberately raises. None were detectable by reading the generated test, only by running it and understanding *why* it failed. AI assistance is an accelerant for coverage and edge-case breadth, not a substitute for judgement about what a correct assertion means: a test that passes is evidence only if a human has decided what passing signifies.

### 2.9 Capstone: scalable architecture, robustness and AI-readiness

The capstone is a **transaction risk-assessment engine**: transactions in, governance-instrumented decisions out. *Scalability:* feature computation is pure and parallelisable, caching is an inner decorator replaceable by a distributed cache, the registry centralises lifecycle state, and provider families isolate network mechanics behind one callable per family — the seam where production would insert pooling, timeouts and routing. *Robustness:* 355 tests, 99% coverage, deterministic by construction (fixed clock, fixed UTC timestamp, no sleeping, no network), asserting degradation reaching the audit trail, redaction before any payload is seen, malformed replies rejected, promotion changing attribution, and illegal transitions refused. *AI-readiness:* abstraction via `ScoringBackend`, contracts via `StrategyOutput` and versioned `FeatureDefinition`, extensibility via a registry that makes model version a runtime fact. *Running-system evidence:* `evidence/demo_run_output.txt` captures eight sections — composition root, a six-transaction batch yielding two `allow`, three `review` and one `block`, four reports over one unchanged batch, six audit records containing no identifier, masking before egress (`GB33BUKB20201555555555` → `******************5555`), provider outage with a three-attempt retry budget and explicit degradation, hostile-payload rejection, model promotion with lifecycle enforcement, and container-resolved singletons.

### 2.10 Cross-cutting critical commentary

**Trade-offs.** Every pattern was chosen against a rejected alternative, and the rejections are part of the work: Strategy over a scorer hierarchy, Decorator over subclass combinations, Visitor over report methods on the elements, Abstract Factory over a factory function, constructor injection over a container inside the domain. **Testing complexity.** Both defects are cases where tests were individually correct and the system collectively wrong — an ordering defect invisible to per-decorator tests, and a missing state mutation invisible to a return-value test. **Abstraction versus over-engineering.** Every abstraction required a rejected alternative and a stated cost; the sixth decorator was rejected, and the minimal container is the seam for provider selection rather than a general-purpose framework.

---

## Task 3 — Reflection on Skill Development

### 3.1 Academic skills

An e-Portfolio is a curated, purposeful collection of evidence rather than a chronological stack of exercises (Lorenzo and Ittelson, 2005), which is why every claim below is tied to a named artefact or capture rather than asserted. **Critical thinking.** The formative experience was the composition-order defect. My first reaction was to distrust the test; the correct reaction was to distrust the design, because a failing assertion about whether degraded decisions reach the audit trail is a statement about *policy*. I now ask what a passing test *cannot* see before asking whether it should pass — and applied to the registry defect, that question produced my working rule: for any component owning state, at least one test must observe that state through a different access path from the one it mutated.

**Ethical considerations in AI-driven OO design.** *Bias propagation:* the structural defence is that policy lives in data rather than a class hierarchy — rule weights, thresholds, `ELEVATED_RISK_CATEGORIES` and channel weights are inspectable, versionable values, so a bias review is a review of a table rather than archaeology inside a code branch. The residual risk is real: those weights are expert-set rather than learned, so they encode the judgement and blind spots of whoever set them, and `channel_risk` and category weights are exactly the kind of proxies that can carry demographic bias. The engine makes bias *inspectable*; it cannot make it *absent*. *Transparency:* `rationale` and `contributions` are contract fields, so an algorithm that cannot say which inputs drove its number cannot conform to the interface, and the audit record carries the rationale verbatim. But explainability is only as good as the algorithm behind it, so I would treat a provider's narrative as untrusted content, sanitised before display, rather than as fact. *Designer responsibility for testability:* injecting transport, clock, context provider and sink is what allows the remote-model path to be tested offline; the ethical weight is not the coverage percentage but that a decision-affecting system which cannot be tested cannot be trusted, and since these decisions affect access to people's own money, untestable design is a harm rather than an inconvenience. *Avoiding black-box coupling:* `ScoringBackend` is five lines with no vendor concepts, and no domain module imports a vendor SDK, so the coupling is a revocable composition-root decision. *Auditability:* every decision produces a structured record — component, model version, feature count, score, decision, rationale — which the run asserts contains no account or customer identifier.

**Problem-solving and refactoring.** Refactoring was driven by measurable signals rather than taste: extracting `validation.py` removed duplication across five modules and made the security rules auditable in one file, and extracting `compose_scoring_pipeline()` collapsed four copies of an ordering decision into one documented function — which is what made the ordering defect visible at all. I also resisted over-refactoring: the reporting visitors tempt a shared base class, but their `result()` shapes differ deliberately, so a common base would have created inheritance without polymorphism.

**Research and self-study.** Reading fell into three groups: pattern and principle sources (Gamma et al., 1994; Martin, 2000, 2017; Fowler, 2004, 2018) for vocabulary and named trade-offs; AI-systems sources (Sculley et al., 2015; Mitchell et al., 2019) for the feature-store and registry arguments and the framing of hidden technical debt; and risk and fairness sources (Aven and Thekdi, 2025; Barocas et al., 2023) to keep the design anchored to what a risk score actually asserts. The most useful outcome was negative: none of the pattern literature treats *governance* layers — audit, redaction, degradation — as first-class composition concerns, so the pipeline-order reasoning here came from analysing my own failure modes.

### 3.2 Employability skills

**Time management.** No single session could hold the whole assessment, so I worked in artefact-sized increments — one pattern, one test module, one evidence capture — keeping the four tasks visible as a checklist so evidence capture happened alongside construction rather than being reconstructed at the end. My concrete failure was underestimating test-writing: closing coverage from 87% to 99% took three new modules (143 tests) and produced the two most valuable findings in the portfolio, which argues for budgeting test time as design time.

**Communication and literacy.** The clearest gain was writing docstrings that carry a decision rather than a description: `compose_scoring_pipeline` states the order and justifies each position, and `_ALLOWED_TRANSITIONS` documents why `ARCHIVED` is terminal. Writing the degradation marker as a machine-readable prefix (`[degraded: primary unavailable]`) was also a communication decision, since the same form is legible to an operator, a downstream consumer and a test.

**Resilience and interpersonal skills.** Resilience meant sitting with a failing test I believed was correct for longer than was comfortable, and changing the design instead of the assertion. On collaboration I must be explicit about the limits of my evidence: this portfolio is submitted as individual work, and Appendix B records the collaboration evidence I hold and what must be attached from personal records, because that evidence cannot be reconstructed after the fact and asserting collaboration that did not occur would be an academic integrity failure. The professional value was nonetheless real — being asked to review another student's decorator implementation forced me to articulate my own ordering decision out loud, revealing that I had been relying on an intuition I had never written down.

---

## Task 4 — Professional Development Plan

### 4.1 Strengths and areas for improvement

| Strengths (evidenced) | Areas for improvement (evidenced) |
|---|---|
| Nine modules with clear boundaries and one composition root naming concrete types | Documentation of intent is uneven — reasoning sits in docstrings and commentary, not one design record |
| Test discipline as a design tool: 355 offline, deterministic tests at 99% coverage | Test-*shape* thinking arrived late; two defects escaped individually correct tests |
| Security as structure: validated immutable domain objects, boundary redaction, untrusted replies rejected | Data-side depth: fairness metrics and drift detection understood conceptually, not implemented |
| Registry and feature store implemented, not merely named | Operational thinking: retries, fallback and audit exist, but no monitoring or drift-visibility artefact |
| Honest scoping: abstractions rejected, residual risks documented | Early drafts were descriptive and had to be cut towards reasoning |

### 4.2 Future goals and actions

**Goal 1 — Deepen expertise in AI-aware software architecture.** *Certification:* AWS Certified Machine Learning Engineer – Associate, because it forces the operational vocabulary — monitoring, endpoint scaling, drift — that my design currently assumes someone else provides; pragmatically, first the shorter AWS Certified Cloud Practitioner, then the ML Engineer – Associate next term. *Reading:* Sculley et al. (2015) again with a design-review lens, Kleppmann (2017) for the distributed-systems reasoning a durable registry will need, and the MLflow model-registry (MLflow, 2026) and Feast feature-store (Feast, 2026) documentation as reference implementations to compare against my interfaces. *Applied project:* a persistent registry with an append-only promotion audit, and a drift-detection report added as a **new visitor** — the cleanest demonstration that the Visitor pattern's extension claim is real.

**Goal 2 — Advanced testing strategies for intelligent systems.** *Reading and practice:* property-based testing with Hypothesis (Hypothesis, 2026), because the defect classes I hit — missing state mutation, wrong composition order — are exactly what property-based and metamorphic testing target; a property such as "for every promotion, the registry's production version equals the last promoted version" would have caught the registry defect without my having to think of that assertion. *Skill:* contract testing for `ScoringBackend` so every future provider is validated against the same behavioural contract, plus shadow-evaluation tooling so a candidate model can be scored against live traffic without affecting decisions. *Applied project:* an evaluation harness comparing `rules-v1` against a trained model on held-out data, reporting accuracy and fairness metrics per subgroup, to measure the residual risk of expert-set weights identified in Task 3.

**Goal 3 — Ethical software engineering in AI-integrated applications.** *Reading:* Barocas et al. (2023) properly rather than selectively; the NIST AI Risk Management Framework (NIST, 2023) as a governance structure for the audit and registry design; and the EU AI Act's high-risk obligations (European Parliament and Council, 2024), since credit scoring is a named high-risk category and the design decisions here — auditability, human review, documented model lifecycle — are what that framework demands. *Practice:* adopt model cards (Mitchell et al., 2019) as required artefacts for every registered version, so documented intended use becomes a structural gate: extend `ModelRecord` with a required promotion rationale and refuse `PRODUCTION` promotion without it. *Applied project:* implement the fairness evaluation as a fifth reporting visitor and treat its output as a review gate for threshold changes, making bias a repeated measurement rather than a one-off conversation.

### 4.3 Reflective model applied — Gibbs' Reflective Cycle

Gibbs (1988) frames the event that produced the most learning: the composition-order defect. Kolb's (1984) cycle, the Johari Window (Luft and Ingham, 1955) and Bloom's revised taxonomy (Anderson and Krathwohl, 2001) were the alternatives; Gibbs was chosen because the trigger is one bounded failure whose cause can be traced, rather than a question of learning style or skills level.

**1. Description.** I had assembled the pipeline and written a test module per decorator; all passed. I then wrote a test asserting that when the hosted provider is unreachable, the degraded decision still reaches the audit trail. It failed: the sink recorded nothing for that transaction.

**2. Feelings.** First frustration and a suspicion the new test was wrong, because the per-decorator tests were green and the code read correctly. That gave way to unease on inspecting the assembly code, because the ordering was plainly visible and I had looked at it repeatedly without seeing the problem. The discomfort marked the moment my mental model shifted from "a chain of layers" to "a chain of layers *with governance positioned relative to failure*".

**3. Evaluation.** Two things went wrong. The immediate cause was that `AuditingDecorator` wrapped the strategy before `FallbackDecorator` wrapped the result, so the substituted decision was produced outside the auditor. The deeper cause was that four places assembled the pipeline, making the ordering an implicit convention nobody could review. The injectable sink made the failure observable at all — without it, the defect would have surfaced in production as an incident rather than as a design error.

**4. Analysis.** This connects directly to the module's pattern content. The Decorator pattern's strength — composable, independent layers — is also its characteristic hazard: correctness depends on ordering, and ordering is invisible to the type system because every decorator shares one type. Consolidating assembly into one documented function converts a hidden convention into a reviewable artefact. Mitchell et al. (2019) argue a model's intended use should be a first-class artefact; the equivalent for a governance pipeline is that each layer's *position relative to failure* deserves the same explicitness, and I had treated it as an implementation detail. It also changed how I read the brief's phrase "separation of concerns": that is satisfied not by having separate classes but by stating and enforcing each concern's relationship to the others.

**5. Conclusion.** Individually correct tests can sum to a collectively incorrect system, and that is a design smell rather than a testing gap. Two rules followed and now apply in this codebase: governance layers are positioned relative to failure modes with the positioning documented at the assembly point; and every stateful component has at least one test reading state back through a different path from the one it wrote through.

**6. Action plan.** Next project I will (i) write the assembly function before the individual layers, so the ordering is decided once and deliberately; (ii) write one integration test per *failure mode* rather than per class, as soon as the pipeline assembles; and (iii) re-read every assembly point at review asking "what does this ordering make invisible?". Kolb's (1984) cycle frames the same sequence — concrete experience, reflective observation, abstract conceptualisation, active experimentation — and the abstracted principle above is the conceptualisation the action plan now tests.

---

## References

Anderson, L.W. and Krathwohl, D.R. (2001) *A Taxonomy for Learning, Teaching, and Assessing: A Revision of Bloom's Taxonomy of Educational Objectives*. New York: Longman.

Aven, T. and Thekdi, S. (2025) *Risk Science: An Introduction*. 2nd edn. Abingdon: Routledge.

Barocas, S., Hardt, M. and Narayanan, A. (2023) *Fairness and Machine Learning: Limitations and Opportunities*. Cambridge, MA: MIT Press.

European Parliament and Council (2024) Regulation (EU) 2024/1689 of the European Parliament and of the Council laying down harmonised rules on artificial intelligence (Artificial Intelligence Act). *Official Journal of the European Union*, L 1689. Available at: https://eur-lex.europa.eu/eli/reg/2024/1689/oj (Accessed: 17 October 2026).

Feast (2026) *Feast: Feature Store for Machine Learning*. Available at: https://docs.feast.dev/ (Accessed: 17 October 2026).

Fowler, M. (2004) *Inversion of Control Containers and the Dependency Injection Pattern*. Available at: https://martinfowler.com/articles/injection.html (Accessed: 17 October 2026).

Fowler, M. (2018) *Refactoring: Improving the Design of Existing Code*. 2nd edn. Boston, MA: Addison-Wesley.

Gamma, E., Helm, R., Johnson, R. and Vlissides, J. (1994) *Design Patterns: Elements of Reusable Object-Oriented Software*. Boston, MA: Addison-Wesley.

Gibbs, G. (1988) *Learning by Doing: A Guide to Teaching and Learning Methods*. Oxford: Further Education Unit, Oxford Polytechnic.

Hypothesis (2026) *Hypothesis: Property-Based Testing for Python*. Available at: https://hypothesis.readthedocs.io/ (Accessed: 17 October 2026).

Kleppmann, M. (2017) *Designing Data-Intensive Applications*. Sebastopol, CA: O'Reilly Media.

Kolb, D.A. (1984) *Experiential Learning: Experience as the Source of Learning and Development*. Englewood Cliffs, NJ: Prentice Hall.

Lorenzo, G. and Ittelson, J. (2005) *An Overview of E-Portfolios*. Boulder, CO: Educause Learning Initiative.

Luft, J. and Ingham, H. (1955) 'The Johari window: a graphic model of interpersonal awareness', *Proceedings of the Western Training Laboratory in Group Development*. Los Angeles: University of California.

Martin, R.C. (2000) *Design Principles and Design Patterns*. Available at: https://web.archive.org/web/20150906155800/http://www.objectmentor.com/resources/articles/Principles_and_Patterns.pdf (Accessed: 17 October 2026).

Martin, R.C. (2017) *Clean Architecture: A Craftsman's Guide to Software Structure and Design*. Boston, MA: Prentice Hall.

Mitchell, M., Wu, S., Zaldivar, A., Barnes, P., Vasserman, L., Hutchinson, B., Spitzer, E., Raji, I.D. and Gebru, T. (2019) 'Model cards for model reporting', *Proceedings of the Conference on Fairness, Accountability, and Transparency (FAT\* '19)*, pp. 220–229. doi:10.1145/3287560.3287596.

MLflow (2026) *MLflow Model Registry*. Available at: https://mlflow.org/docs/latest/model-registry.html (Accessed: 17 October 2026).

National Institute of Standards and Technology (NIST) (2023) *Artificial Intelligence Risk Management Framework (AI RMF 1.0)*. NIST AI 100-1. Gaithersburg, MD: NIST.

OWASP Foundation (2021) *OWASP Top 10:2021 – The Ten Most Critical Web Application Security Risks*. Available at: https://owasp.org/Top10/ (Accessed: 17 October 2026).

Python Software Foundation (2026) *unittest.mock — mock object library*. Available at: https://docs.python.org/3/library/unittest.mock.html (Accessed: 17 October 2026).

Sculley, D., Holt, G., Golovin, D., Davydov, E., Phillips, T., Ebner, D., Chaudhary, V., Young, M., Crespo, J.-F. and Dennison, D. (2015) 'Hidden technical debt in machine learning systems', *Advances in Neural Information Processing Systems 28 (NIPS 2015)*, pp. 2503–2511.

---

## Appendix A — Artefact and evidence index

| # | Artefact | File(s) | Evidence | Task 2 |
|---|---|---|---|---|
| A | Thread-safe coding exercise (Unit 6) | `unit6/bank_account.py`, `unit6/transaction_simulator.py`, `unit6/test_banking_system.py` | 39 tests passing; simulator run output | 2.1 |
| B | Strategy pattern | `capstone/strategies.py` | `test_strategies.py`; run section 2 | 2.2 |
| C | Decorator pattern | `capstone/decorators.py` | `test_decorators.py`; run section 6a | 2.3 |
| D | Visitor pattern | `capstone/reporting.py` | `test_reporting.py`; run section 3 | 2.4 |
| E | Abstract Factory | `capstone/providers.py` | `test_providers.py`; run sections 6b–6c | 2.5 |
| F | Model Registry | `capstone/model_registry.py` | `test_model_registry.py`; run section 7 | 2.6 |
| G | Feature Store | `capstone/feature_store.py` | `test_feature_store.py`; run section 2 | 2.6 |
| H | DI / IoC | `capstone/service.py` | `test_service.py`; run section 8 | 2.7 |
| I | Mocking / AI-driven testing | `capstone/tests/conftest.py` + nine test modules | `evidence/test_run_output.txt`; `evidence/coverage_report.txt` | 2.8 |
| J | Secure validation layer | `capstone/validation.py`, `capstone/domain.py` | `test_validation.py`, `test_domain.py`; run section 5 | 1.1 |
| K | Runtime demonstration | `demo_run.py` | `evidence/demo_run_output.txt` (8 sections) | 2.9 |
| L | Coverage report | — | `evidence/coverage_html/index.html` | 2.8 |

## Appendix B — Collaboration and integrity evidence (student to complete)

Twenty per cent of the module mark depends on evidence of independent working, or of individual contribution to a group project, and that evidence must be the student's own real trace. Nothing here has been generated on the student's behalf; the table is a checklist to complete, using the summary in `integrity_and_collaboration_notes.md` and the log in `contribution_log_template.md`.

| Evidence type | What to attach | Held? |
|---|---|---|
| Repository contribution history | Git log with your commits, dates and messages, or `git shortlog -sne` output | Yes / No |
| Task ownership | Task board export (Trello/Jira/GitHub Projects) showing items you owned and their state | Yes / No |
| Meeting records | Dated minutes or notes with your recorded actions | Yes / No |
| Peer review | Comments you gave and received on another student's artefact | Yes / No |
| Tutor feedback | Feedback on drafts, with your response to it | Yes / No |
| Independent working | Dated study log or signed planner extract showing your own schedule | Yes / No |

## Appendix C — Honest statement of limits

1. **The default rule weights are expert judgement, not evidence.** The weights and the elevated-category set are plausible values, not values derived from data. They are versioned and inspectable, but inspectable is not validated, and the fairness risk described in Task 3 lives here.
2. **The model-backed path is exercised against test doubles, never a real model.** This is deliberate — no API keys, no cost, no network — and it validates the integration contract thoroughly. It does not validate that any real model answers correctly; the hosted transport is the seam where that would change.
3. **The registry and feature store are in-memory.** They demonstrate lifecycle and versioning semantics with concurrency safety, but durability, migration and multi-process consistency are out of scope, which is why Goal 1 names a persisted registry as the next applied project.
