"""
Model Registry -- lifecycle and version management for scoring models.

Why this exists
---------------
An AI-enabled object has a lifecycle the ordinary object does not.  A
``BankAccount`` is created and then simply is; a *model* is trained, staged,
validated, promoted, monitored, superseded and eventually archived, and at
every point someone must be able to answer "which model produced the
decision that declined this transaction at 14:03 yesterday?".

Without a registry, that answer lives in a deployment script, a Slack
message or a data-scientist's memory.  The registry makes it a query.

Two invariants are enforced here because both are audit requirements, not
conveniences:

1. **Exactly one production version per model name.**  Promoting a new
   version retires the incumbent automatically.  Two simultaneously live
   production versions would make the score non-reproducible, which defeats
   the purpose of recording a model version at all.
2. **Stage transitions are a closed, validated state machine.**  A model
   cannot jump from development straight to production, because the
   intervening staging stage is where validation evidence is attached.  An
   illegal transition raises rather than being quietly permitted.

Note on reuse: the registry is itself thread-safe using the same
single-lock discipline as the Unit 6 banking artefact.  Concurrent worker
threads may register or promote models, and a check-then-act sequence on
the "one production version" rule would otherwise be a classic
time-of-check/time-of-use race.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Iterator, Mapping

from .validation import (
    ModelLifecycleError,
    ModelNotFoundError,
    ValidationError,
    validate_text,
)


class ModelStage(Enum):
    """The lifecycle stage a model version currently occupies."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


# The permitted transition graph.  Encoding it as data rather than as a
# chain of if-statements means the policy is inspectable, testable and
# reviewable by a non-programmer -- an auditability property.
_ALLOWED_TRANSITIONS: Mapping[ModelStage, frozenset[ModelStage]] = MappingProxyType(
    {
        ModelStage.DEVELOPMENT: frozenset({ModelStage.STAGING, ModelStage.ARCHIVED}),
        ModelStage.STAGING: frozenset({ModelStage.PRODUCTION, ModelStage.ARCHIVED}),
        # PRODUCTION -> STAGING is a deliberate rollback path.
        ModelStage.PRODUCTION: frozenset({ModelStage.ARCHIVED, ModelStage.STAGING}),
        # ARCHIVED is terminal: an archived model is never resurrected, so a
        # historical decision can always be explained by the model that was
        # live at the time.
        ModelStage.ARCHIVED: frozenset(),
    }
)


@dataclass(frozen=True, slots=True)
class ModelRecord:
    """An immutable registry entry describing one version of one model."""

    name: str
    version: str
    stage: ModelStage
    artifact_ref: str
    metrics: Mapping[str, float] = field(default_factory=dict)
    registered_by: str = "unknown"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        validate_text(self.name, "model name", max_length=64)
        validate_text(self.version, "model version", max_length=32)
        validate_text(self.artifact_ref, "artifact_ref", max_length=256)
        if not isinstance(self.stage, ModelStage):
            raise ValidationError("stage must be a ModelStage member")
        for key, value in self.metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValidationError(f"metric {key!r} must be numeric")

    @property
    def qualified_name(self) -> str:
        """Return ``name:version`` -- the identifier quoted in an audit record."""
        return f"{self.name}:{self.version}"

    def metric(self, key: str, default: float = 0.0) -> float:
        """Read a validation metric, e.g. ``roc_auc`` or ``precision_at_90``."""
        return float(self.metrics.get(key, default))


class ModelRegistry:
    """A thread-safe, in-memory model registry with an enforced lifecycle."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, ModelRecord]] = {}

    # -- registration -------------------------------------------------

    def register(
        self,
        name: str,
        version: str,
        artifact_ref: str,
        metrics: Mapping[str, float] | None = None,
        registered_by: str = "unknown",
    ) -> ModelRecord:
        """Register a new model version in DEVELOPMENT.

        Every model enters via DEVELOPMENT: there is no "register directly
        into production" path, which removes an entire class of operator
        mistake.
        """
        record = ModelRecord(
            name=name,
            version=version,
            stage=ModelStage.DEVELOPMENT,
            artifact_ref=artifact_ref,
            metrics=dict(metrics or {}),
            registered_by=registered_by,
        )
        with self._lock:
            versions = self._records.setdefault(record.name, {})
            if record.version in versions:
                raise ModelLifecycleError(
                    f"model {name!r} version {version!r} is already registered"
                )
            versions[record.version] = record
            return record

    # -- lifecycle ----------------------------------------------------

    def promote(self, name: str, version: str, to_stage: ModelStage) -> ModelRecord:
        """Move *version* to *to_stage*, enforcing the transition graph.

        Promoting to PRODUCTION atomically archives any incumbent production
        version, so the "exactly one live production model" invariant holds
        even under concurrent promotion attempts.
        """
        if not isinstance(to_stage, ModelStage):
            raise ValidationError("to_stage must be a ModelStage member")

        with self._lock:
            current = self._require(name, version)
            allowed = _ALLOWED_TRANSITIONS[current.stage]
            if to_stage not in allowed:
                raise ModelLifecycleError(
                    f"illegal transition {current.stage.value} -> {to_stage.value} "
                    f"for model {name!r} version {version!r}"
                )

            if to_stage is ModelStage.PRODUCTION:
                self._retire_incumbent_production(name, excluding_version=version)

            updated = self._replace(current, to_stage)
            # The replacement must be written back into the registry.
            # Returning it without storing it makes the promotion look
            # successful to the caller while every later query still sees
            # the old stage -- a lifecycle control that silently does
            # nothing.  (Found by the test suite; see commentary.)
            self._records[name][version] = updated
            return updated

    # -- queries ------------------------------------------------------

    def get(self, name: str, version: str) -> ModelRecord:
        """Return one specific version, or raise :class:`ModelNotFoundError`."""
        with self._lock:
            return self._require(name, version)

    def get_production(self, name: str) -> ModelRecord:
        """Return the single production version of *name*.

        The scoring service depends on this method, and it is the reason a
        strategy never hard-codes a model version: the strategy asks the
        registry what is live right now.
        """
        with self._lock:
            versions = self._records.get(name)
            if not versions:
                raise ModelNotFoundError(f"model {name!r} is not registered")
            for record in versions.values():
                if record.stage is ModelStage.PRODUCTION:
                    return record
            raise ModelNotFoundError(
                f"model {name!r} has no production version; promotion is required first"
            )

    def history(self, name: str) -> list[ModelRecord]:
        """Return every version of *name*, oldest first."""
        with self._lock:
            versions = self._records.get(name)
            if not versions:
                raise ModelNotFoundError(f"model {name!r} is not registered")
            return sorted(
                versions.values(), key=lambda record: (record.created_at, record.version)
            )

    def by_stage(self, stage: ModelStage) -> list[ModelRecord]:
        """Return every record currently in *stage*, across all model names."""
        with self._lock:
            return [
                record
                for versions in self._records.values()
                for record in versions.values()
                if record.stage is stage
            ]

    def __iter__(self) -> Iterator[ModelRecord]:
        with self._lock:
            snapshot = [
                record
                for versions in self._records.values()
                for record in versions.values()
            ]
        return iter(sorted(snapshot, key=lambda r: r.qualified_name))

    # -- internals ----------------------------------------------------

    def _require(self, name: str, version: str) -> ModelRecord:
        """Fetch a record, translating a missing key into a domain error."""
        try:
            return self._records[name][version]
        except KeyError as exc:
            raise ModelNotFoundError(
                f"model {name!r} version {version!r} is not registered"
            ) from exc

    def _retire_incumbent_production(self, name: str, excluding_version: str) -> None:
        """Archive any other production version of *name* (call within lock)."""
        versions = self._records.get(name, {})
        for record in list(versions.values()):
            if (
                record.stage is ModelStage.PRODUCTION
                and record.version != excluding_version
            ):
                versions[record.version] = self._replace(record, ModelStage.ARCHIVED)

    @staticmethod
    def _replace(record: ModelRecord, stage: ModelStage) -> ModelRecord:
        """Return a copy of *record* in *stage* with a refreshed ``updated_at``."""
        return ModelRecord(
            name=record.name,
            version=record.version,
            stage=stage,
            artifact_ref=record.artifact_ref,
            metrics=record.metrics,
            registered_by=record.registered_by,
            created_at=record.created_at,
            updated_at=datetime.now(timezone.utc),
        )


__all__ = ["ModelRecord", "ModelRegistry", "ModelStage"]
