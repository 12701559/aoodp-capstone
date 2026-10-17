"""Tests for the Model Registry.

The registry is what makes an AI decision reproducible: it records which
version of which model produced each verdict, and it enforces the lifecycle
rules that stop an unvalidated model reaching production.  Both properties
are asserted here as invariants rather than as incidental behaviour.
"""

from __future__ import annotations

import pytest

from capstone.model_registry import ModelRegistry, ModelStage
from capstone.validation import (
    ModelLifecycleError,
    ModelNotFoundError,
    ValidationError,
)


@pytest.fixture()
def registry() -> ModelRegistry:
    return ModelRegistry()


def _register(
    registry: ModelRegistry,
    version: str = "1.0.0",
    name: str = "transaction-risk",
    **overrides: object,
):
    kwargs: dict[str, object] = {
        "name": name,
        "version": version,
        "artifact_ref": f"s3://risk-models/{name}/{version}/model.pkl",
        "metrics": {"roc_auc": 0.91},
        "registered_by": "test-suite",
    }
    kwargs.update(overrides)
    return registry.register(**kwargs)  # type: ignore[arg-type]


class TestRegistration:
    def test_new_models_enter_development(self, registry: ModelRegistry) -> None:
        record = _register(registry)
        assert record.stage is ModelStage.DEVELOPMENT

    def test_qualified_name_pairs_name_and_version(self, registry: ModelRegistry) -> None:
        assert _register(registry).qualified_name == "transaction-risk:1.0.0"

    def test_duplicate_version_is_refused(self, registry: ModelRegistry) -> None:
        _register(registry)
        with pytest.raises(ModelLifecycleError):
            _register(registry)

    def test_metric_lookup_has_a_default(self, registry: ModelRegistry) -> None:
        record = _register(registry)
        assert record.metric("roc_auc") == 0.91
        assert record.metric("unrecorded") == 0.0

    def test_multiple_versions_coexist(self, registry: ModelRegistry) -> None:
        _register(registry, version="1.0.0")
        _register(registry, version="2.0.0")
        assert registry.get("transaction-risk", "1.0.0").version == "1.0.0"
        assert len(registry.history("transaction-risk")) == 2


class TestLifecycleTransitions:
    def test_development_to_staging_to_production(self, registry: ModelRegistry) -> None:
        _register(registry)
        assert registry.promote("transaction-risk", "1.0.0", ModelStage.STAGING).stage \
            is ModelStage.STAGING
        promoted = registry.promote("transaction-risk", "1.0.0", ModelStage.PRODUCTION)
        assert promoted.stage is ModelStage.PRODUCTION
        assert registry.get_production("transaction-risk").version == "1.0.0"

    def test_development_cannot_jump_straight_to_production(
        self, registry: ModelRegistry
    ) -> None:
        """There is no path that skips validation."""
        _register(registry)
        with pytest.raises(ModelLifecycleError):
            registry.promote("transaction-risk", "1.0.0", ModelStage.PRODUCTION)

    def test_archived_is_terminal(self, registry: ModelRegistry) -> None:
        """A model that made a historical decision must stay explainable."""
        _register(registry)
        registry.promote("transaction-risk", "1.0.0", ModelStage.ARCHIVED)
        for target in (ModelStage.STAGING, ModelStage.PRODUCTION):
            with pytest.raises(ModelLifecycleError):
                registry.promote("transaction-risk", "1.0.0", target)

    def test_production_can_roll_back_to_staging(self, registry: ModelRegistry) -> None:
        _register(registry)
        registry.promote("transaction-risk", "1.0.0", ModelStage.STAGING)
        registry.promote("transaction-risk", "1.0.0", ModelStage.PRODUCTION)
        rolled_back = registry.promote(
            "transaction-risk", "1.0.0", ModelStage.STAGING
        )
        assert rolled_back.stage is ModelStage.STAGING

    def test_only_one_production_version_remains_live(
        self, registry: ModelRegistry
    ) -> None:
        """The incumbent is archived atomically, so the service never has to
        choose between two live models."""
        _register(registry, version="1.0.0")
        registry.promote("transaction-risk", "1.0.0", ModelStage.STAGING)
        registry.promote("transaction-risk", "1.0.0", ModelStage.PRODUCTION)

        _register(registry, version="2.0.0")
        registry.promote("transaction-risk", "2.0.0", ModelStage.STAGING)
        registry.promote("transaction-risk", "2.0.0", ModelStage.PRODUCTION)

        assert registry.get_production("transaction-risk").version == "2.0.0"
        assert registry.get("transaction-risk", "1.0.0").stage is ModelStage.ARCHIVED
        production = registry.by_stage(ModelStage.PRODUCTION)
        assert [record.version for record in production] == ["2.0.0"]

    def test_promote_requires_a_stage_member(self, registry: ModelRegistry) -> None:
        _register(registry)
        with pytest.raises(ValidationError):
            registry.promote("transaction-risk", "1.0.0", "production")  # type: ignore[arg-type]


class TestQueries:
    def test_unknown_model_and_version_raise(self, registry: ModelRegistry) -> None:
        with pytest.raises(ModelNotFoundError):
            registry.get("never-registered", "1.0.0")
        _register(registry)
        with pytest.raises(ModelNotFoundError):
            registry.get("transaction-risk", "9.9.9")

    def test_get_production_before_promotion_is_an_error(
        self, registry: ModelRegistry
    ) -> None:
        """Absence of a live model is a configuration failure, not a
        default -- silently picking a DEVELOPMENT model would be worse."""
        _register(registry)
        with pytest.raises(ModelNotFoundError):
            registry.get_production("transaction-risk")

    def test_get_production_for_unknown_model_raises(
        self, registry: ModelRegistry
    ) -> None:
        with pytest.raises(ModelNotFoundError):
            registry.get_production("never-registered")

    def test_history_and_iteration(self, registry: ModelRegistry) -> None:
        _register(registry, version="1.0.0")
        _register(registry, version="1.1.0")
        assert [r.version for r in registry.history("transaction-risk")] == [
            "1.0.0",
            "1.1.0",
        ]
        assert len(list(iter(registry))) == 2

    def test_history_of_an_unknown_model_raises(self, registry: ModelRegistry) -> None:
        """Deliberately not an empty list.  An empty history is a plausible
        answer for a model whose first version was never promoted, so
        returning one for a misspelt name would hide the operator error; the
        consistent answer is the same domain error as every other lookup."""
        with pytest.raises(ModelNotFoundError):
            registry.history("never-registered")
        assert registry.by_stage(ModelStage.PRODUCTION) == []

    def test_records_are_immutable(self, registry: ModelRegistry) -> None:
        """A promotion replaces the record; it never edits one in place."""
        from dataclasses import FrozenInstanceError

        record = _register(registry)
        with pytest.raises(FrozenInstanceError):
            record.stage = ModelStage.PRODUCTION  # type: ignore[misc]
