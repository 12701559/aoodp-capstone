# AOODP EMA e-Portfolio — Transaction Risk-Assessment Engine

Advanced Object-Oriented Design and Programming (AOODP) — End-of-Module Assessment submission.

**Student:** 12701559 · **Submitted:** 17 October 2026

This repository is the artefact evidence for the e-Portfolio. The submission document
(`docs/AOODP_EMA_eportfolio_12701559.pdf`) cites the files below by path; Appendix A of that
document is the authoritative artefact-to-file index.

---

## 1. What the capstone is

A transaction risk-assessment engine: transactions in, governance-instrumented decisions out.
It is deliberately small enough to reason about and structured enough to demonstrate advanced
object-oriented principles rather than restate them.

| Pattern / principle | Module | What it buys |
|---|---|---|
| Strategy | `capstone/strategies.py` | Rules, statistical and model-backed scoring swapped by one config string |
| Decorator | `capstone/decorators.py` | Redaction, metrics, audit, caching and fallback composed at one assembly point |
| Visitor | `capstone/reporting.py` | A new report is one class and no edit to the element hierarchy |
| Abstract Factory | `capstone/providers.py` | A whole AI-provider family swapped together, with the family relationship checked |
| Dependency inversion | `capstone/service.py` | Every outside-world dependency injected; tests are deterministic and offline |
| Fail-fast encapsulation | `capstone/domain.py`, `capstone/validation.py` | An invalid transaction cannot be constructed |
| Lifecycle state machine | `capstone/model_registry.py` | Model version is a runtime fact, with illegal transitions refused |
| Feature contracts | `capstone/feature_store.py` | Named, versioned, owned feature definitions; train/serve skew controlled |

## 2. Layout

```
aoodp-capstone/
├── capstone/            nine modules: domain, validation, strategies, decorators,
│                        reporting, providers, model_registry, feature_store, service
│   └── tests/           conftest.py (stubs, a fake, spies) + nine test modules
├── unit6/               companion thread-safe banking artefact (Unit 6, reused)
├── demo_run.py          eight-section runnable evidence demonstration
├── evidence/            captured test run, coverage report and HTML, demo output
├── docs/                the e-Portfolio submission (PDF + Word), narrative and figure
└── tools/               architecture-diagram generator, citation checker
```

## 3. Reproduce the evidence

Python 3.12+ with `pytest` and `coverage` installed. No network access, no API keys, no model
credentials are required at any point.

```bash
python3 -m pytest capstone/tests unit6 -q                 # 394 passed
python3 -m coverage run -m pytest capstone/tests -q \
  && python3 -m coverage report --skip-covered --include='capstone/*'   # 2,512 stmts / 23 missed / 99%
python3 demo_run.py                                       # eight sections of runtime evidence
```

Verified state of this repository:

| Metric | Value |
|---|---|
| Capstone tests | 355 passed |
| Capstone + Unit 6 tests | 394 passed in ~1.4 s |
| Capstone statements | 2,512, of which 23 missed → 99% |
| External calls during verification | none |

The captures in `evidence/` are the outputs of exactly those commands. The model-backed scoring
path is exercised against test doubles; no real model is called, which is stated as a limitation
in Appendix C of the submission rather than presented as validated behaviour.

## 4. Design decisions worth reading

Three files carry the reasoning that the submission judges most important:

- `capstone/decorators.py` — `compose_scoring_pipeline()` is the single assembly point, and the
  ordering is documented because it is **not** visible to the type system. An earlier arrangement
  placed `AuditingDecorator` inside `FallbackDecorator`, so a degraded decision never reached the
  audit sink. Per-decorator tests all passed; only an integration test per *failure mode* caught it.
- `capstone/model_registry.py` — `promote()` once returned the updated record without writing it
  back into the internal mapping. An assertion on a return value cannot detect a missing state
  mutation, so every transition is now asserted through a second read path (`get_production`,
  `history`).
- `capstone/strategies.py` — `StrategyOutput` keeps `contributions` and `rationale` as contract
  fields, so an algorithm that cannot explain its inputs cannot conform to the interface.

## 5. Integrity

This portfolio is submitted as individual work. The collaboration and task-ownership evidence
required by that part of the assessment is maintained by the student separately from this
repository. AI assistance was used in the manner declared in the submission document.

## 6. Licence and reuse

Coursework produced for assessment. The code is published so the marker can inspect the artefacts
referenced in the submission. Please do not submit it, in whole or in part, as your own work.
