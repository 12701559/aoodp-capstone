# AOODP e-Portfolio — Student 12701559

Advanced Object-Oriented Design and Programming (AOODP) · end-of-module assessment.
This page is the module page for the e-Portfolio. Everything linked below is held in
this repository and served through GitHub Pages.

**Submission documents**

- [e-Portfolio submission (Word, `.docx`)](AOODP_EMA_eportfolio_12701559.docx)
- [e-Portfolio submission (PDF)](AOODP_EMA_eportfolio_12701559.pdf)
- [Full narrative — Tasks 1–4, References, Appendices A–C (`eportfolio_tasks_1-4.md`)](eportfolio_tasks_1-4.md)
- [Narrative body only — Tasks 1–4 with the code excerpts (`submission_body_2500.md`)](submission_body_2500.md)

**Word count:** Tasks 1–4 total 2,422 words, and the e-Portfolio introduction adds 75 words,
giving the counted figure of 2,497 words (guideline: 2,500 words equivalent effort); the
reference list, cover sheet, figure, code excerpts and Appendices A–C are excluded, as are
the listing captions. The document as a whole, excluding the reference list, is 2,861 words.
The reflection (Task 3 with § 4.3) is within the ~1,000-word guidance.

---

## 1. Summary of key learning outcomes (Task 1)

Six learning outcomes, each tied to work in the repository:

| # | Learning outcome | Evidence in this repository |
|---|---|---|
| 1.1 | Understand and implement secure coding practices | `capstone/validation.py`, `capstone/domain.py` — fail-fast construction, PAN masked to `******************5555` before leaving the process |
| 1.2 | Apply advanced object-oriented principles to complex problems | `capstone/service.py` composition root; nine collaborating modules |
| 1.3 | Utilise design patterns for reusable, maintainable, flexible code | Strategy, Decorator, Visitor, Abstract Factory (`capstone/strategies.py`, `decorators.py`, `reporting.py`, `providers.py`) |
| 1.4 | Design architectures suitable for large-scale systems, ensuring security and robustness | Decorator-based retry/governance layers; `FailingBackend` degradation path |
| 1.5 | Develop software adaptable for AI models and efficient for data science tasks | `capstone/providers.py`, `capstone/feature_store.py`, `capstone/model_registry.py` |
| 1.6 | Link theory to practical work | One executed demonstration, `demo_run.py`, exercising all eight evidence sections |

Full discussion: [`eportfolio_tasks_1-4.md` § Task 1](eportfolio_tasks_1-4.md).

## 2. Artefacts, with a brief description (Task 2)

Twelve artefacts, each already executed and backed by a capture in `evidence/`.
The complete index — file paths, lines of code, test counts and per-artefact coverage —
is in [`artefact_index.md`](artefact_index.md).

| Artefact | Pattern / principle | Location | Tests | Coverage |
|---|---|---|---|---|
| A | Concurrency, locks, invariants | `unit6/` | 39 | 97% / 72% / 95% |
| B | Strategy — interchangeable scoring algorithms | `capstone/strategies.py` | 60 | 99% |
| C | Decorator — dynamic governance layers | `capstone/decorators.py` | 36 | 97% |
| D | Visitor — analytics and reporting | `capstone/reporting.py` | 30 | 99% |
| E | Abstract Factory — swapping AI service providers | `capstone/providers.py` | 60 | 97% |
| F | Model Registry — lifecycle / state machine | `capstone/model_registry.py` | 17 | 98% |
| G | Feature Store — derivation and skew control | `capstone/feature_store.py` | 22 | 100% |
| H | Dependency injection and inversion of control | `capstone/service.py` | 53 | 100% |
| I | Test doubles; AI paths validated without live models | `capstone/tests/` | 355 | — |
| J | Secure validation layer | `capstone/validation.py`, `capstone/domain.py` | 77 | 99% / 100% |
| K | Runtime demonstration | `demo_run.py` | run | — |
| L | Coverage report | `evidence/coverage_html/` | — | — |

Repository-wide result for one run: **394 tests passed in 1.36 s**; capstone statement
coverage 2,512 statements / 23 missed / **99%** (`evidence/coverage_report.txt`).

Architecture of the assembled pipeline:

![Capstone architecture](figure_1_architecture.png)

Descriptions, design rationale and cross-cutting critical commentary for each artefact
are in [`eportfolio_tasks_1-4.md` § Task 2](eportfolio_tasks_1-4.md).

### Feedback received

No tutor or peer feedback record is held for these artefacts, so none is claimed here or
in the submission. Appendix B of the submission states this as a factual gap.

## 3. Reflective piece (Task 3)

The reflection runs to approximately 1,000 words when the applied reflective model is
counted with it: **Task 3 (academic and employability skills) plus § 4.3 (Gibbs'
Reflective Cycle)**. It is honest about limits — the rules engine weights are expert-set
with no empirical validation, and that limitation is carried into Appendix C rather than
hidden.

Read it at [`eportfolio_tasks_1-4.md` § Task 3 and § 4.3](eportfolio_tasks_1-4.md).

## 4. Professional skills matrix and action plan (Task 4)

Task 4 sets out a strengths / areas-for-improvement matrix, future goals and the actions
taken against them, then applies Gibbs' Reflective Cycle to the capstone delivery.
See [`eportfolio_tasks_1-4.md` § Task 4](eportfolio_tasks_1-4.md).

## 5. Meeting notes

No meeting minutes or supervision logs form part of this e-Portfolio; Appendix B of the
submission records that as a gap rather than substituting retrospective notes.

## 6. Evidence of independent working

- [`git_contribution_history.txt`](git_contribution_history.txt) — repository history export backing Appendix B
- [`eportfolio_tasks_1-4.md` § Appendix B](eportfolio_tasks_1-4.md) — evidence held in support of independent working
- [`eportfolio_tasks_1-4.md` § Appendix C](eportfolio_tasks_1-4.md) — honest statement of limits

## 7. Referencing

Sources cited in the reflection follow the Harvard (Cite Them Right) style: three-author
works are given in full with *and* before the final author; four or more authors are
abbreviated to the first author plus *et al.* in both the in-text citation and the
reference list. The reference list appears in the submission documents.

---

*Repository: [stacyalwangaessex/aoodp-capstone](https://github.com/stacyalwangaessex/aoodp-capstone)*
