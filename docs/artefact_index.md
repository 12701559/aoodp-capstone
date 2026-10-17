# Artefact and evidence index

Every artefact below exists in this package, was executed, and is backed by a capture in `evidence/`. Nothing here is claimed without a corresponding file.

## 1. Summary

| Metric | Value | Source |
|---|---|---|
| Capstone modules | 9 (plus `__init__` composition surface) | `capstone/*.py` |
| Capstone statements | 2,512 | `evidence/coverage_report.txt` |
| Capstone statements missed | 23 | `evidence/coverage_report.txt` |
| Capstone statement coverage | 99% | `evidence/coverage_report.txt` |
| Capstone tests | 355 | `evidence/test_run_output.txt` collect |
| Unit 6 tests | 39 | `evidence/test_run_output.txt` collect |
| **Total tests, one run** | **394 passed in 1.36s** | `evidence/test_run_output.txt` |
| Combined statements (with unit6) | 2,972 · 63 missed · 98% | `evidence/coverage_report.txt` |
| Demo evidence | 8 sections, 14,559 bytes | `evidence/demo_run_output.txt` |
| External calls during verification | none — no network, no model credentials | demo closing note |

Reproduce all of it:

```bash
python3 -m pytest capstone/tests unit6 -q
python3 -m coverage report --skip-covered --include='capstone/*'
python3 demo_run.py
```

## 2. Code artefacts

| # | Artefact | File | LOC | Pattern / principle | Tests | Coverage | Demo section | Task ref |
|---|---|---|---|---|---|---|---|---|
| A | Thread-safe banking (Unit 6) | `unit6/bank_account.py`, `unit6/transaction_simulator.py`, `unit6/test_banking_system.py` | 283 / 235 / 524 | Concurrency, locks, invariants | 39 | 97% / 72% / 95% | — | 2.1 |
| B | Strategy | `capstone/strategies.py` | 392 | Strategy | 60 | 99% | 2 (rules path) | 2.2 |
| C | Decorator | `capstone/decorators.py` | 518 | Decorator | 36 | 97% | 6a, 6b | 2.3 |
| D | Visitor | `capstone/reporting.py` | 291 | Visitor | 30 | 99% | 3 | 2.4 |
| E | Abstract Factory | `capstone/providers.py` | 473 | Abstract Factory | 60 | 97% | 6b, 6c | 2.5 |
| F | Model Registry | `capstone/model_registry.py` | 273 | Lifecycle / state machine | 17 | 98% | 7 | 2.6 |
| G | Feature Store | `capstone/feature_store.py` | 307 | Feature derivation, skew control | 22 | 100% | 2 | 2.6 |
| H | DI / IoC | `capstone/service.py` | 413 | Dependency injection, composition root | 53 | 100% | 1, 8 | 2.7 |
| I | Mocking / AI-driven testing | `capstone/tests/conftest.py` + ten `test_*.py` modules | 105 (conftest) | Test doubles, offline AI-path validation | 355 total | 95% (conftest) | 6 | 2.8 |
| J | Secure validation layer | `capstone/validation.py`, `capstone/domain.py` | 241 / 198 | Fail-fast construction, data minimisation | 52 / 25 | 99% / 100% | 5 | 1.1 |
| K | Runtime demonstration | `demo_run.py` | 18,792 bytes | Integration evidence | run | — | 1–8 | 2.9 |
| L | Coverage report | `evidence/coverage_html/` + `coverage_report.txt` | — | Test quality evidence | — | — | — | 2.8 |

## 3. Test counts by module (independently collected)

| Test file | Tests | Covers |
|---|---|---|
| `test_strategies.py` | 60 | Artefact B |
| `test_providers.py` | 60 | Artefact E |
| `test_service.py` | 53 | Artefact H |
| `test_validation.py` | 52 | Artefact J |
| `test_decorators.py` | 36 | Artefact C |
| `test_reporting.py` | 30 | Artefact D |
| `test_domain.py` | 25 | Artefact J |
| `test_feature_store.py` | 22 | Artefact G |
| `test_model_registry.py` | 17 | Artefact F |
| `unit6/test_banking_system.py` | 39 | Artefact A |
| **Total** | **394** | |

## 4. Demo sections (`evidence/demo_run_output.txt`)

| § | Title | What it evidences |
|---|---|---|
| 1 | Composition root | The assembled pipeline and its dependency graph (artefact H) |
| 2 | Batch assessment | Six transactions → 2 allow / 3 review / 1 block; scores 0.00, 0.55, 1.00, 0.45, 0.00, 0.65 (artefacts B, G) |
| 3 | Reporting visitors | Four reports over one unchanged batch (artefact D) |
| 4 | Audit trail | One governance record per decision |
| 5 | Privacy boundary | PAN masked to `******************5555` before leaving the process (artefact J) |
| 6 | Resilience | Provider outage → `FailingBackend`, 3 transport attempts (1 initial + 2 retries), `[degraded: primary unavailable]` rationale (artefacts C, E) |
| 7 | Model registry | `transaction-risk:2.1.0` → register 2.2.0 DEVELOPMENT→STAGING→PRODUCTION, 2.1.0 archived; `illegal transition archived -> production` refused (artefact F) |
| 8 | Inversion of control | `singleton risk_service reused: True` (artefact H) |

## 5. Thresholds and weights in the shipped rules engine

Verified from `capstone/strategies.py`, not from a description of it:

| Condition | Weight |
|---|---|
| `amount_zscore > 3.0` | +0.30 |
| `is_foreign > 0.5` | +0.15 |
| `is_elevated_category > 0.5` | +0.20 |
| `is_odd_hour > 0.5` | +0.10 |
| `channel_risk > 0.6` | +0.10 |
| `velocity > 10.0` | +0.25 |
| `log_amount > 6.0` | +0.05 |

These are expert-set values with no empirical validation; that limitation is stated in Appendix C of the e-Portfolio and is the subject of Task 3's fairness discussion. Do not present them as calibrated.

## 6. Cross-check: claim → evidence

Each substantive claim in Tasks 1–4 is traceable:

| Claim in the narrative | Verified against |
|---|---|
| 394 tests pass in one run | `evidence/test_run_output.txt` |
| 2,512 statements, 23 missed, 99% | `evidence/coverage_report.txt` |
| Six transactions, 2/3/1 decision split | `demo_run_output.txt` §2 |
| PAN masked before leaving process | `demo_run_output.txt` §5 |
| Retry budget = 1 initial + 2 retries | `demo_run_output.txt` §6 |
| Illegal registry transition refused | `demo_run_output.txt` §7 |
| Singleton reused across resolutions | `demo_run_output.txt` §8 |
| Weights and thresholds as tabulated | `capstone/strategies.py` (read) |
| No live model or network used | demo closing note; test doubles in `conftest.py` |
