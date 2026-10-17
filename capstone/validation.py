"""
Secure input validation and sanitisation for the risk-assessment engine.

Design rationale
----------------
Every value that enters the engine from the transaction stream is treated as
untrusted.  Validation is centralised here rather than duplicated in each
class, which satisfies two module outcomes at once:

* **Secure coding (Unit 7)** -- the classic OO vulnerabilities are input
  driven.  Rejecting the wrong *type*, non-finite floats and control
  characters at the boundary removes whole classes of downstream failure
  before any business logic runs.
* **DRY / Single Responsibility (Unit 2)** -- the rules live in one place, so
  a change to the account-number format is one edit, not six.

Three specific threats are handled deliberately:

1. **Non-finite floats.**  ``NaN`` compares false to everything, so a naive
   ``if amount < 0`` check silently passes it through and corrupts the
   balance permanently.  ``inf`` breaks every downstream threshold comparison.
2. **Log injection / audit forgery.**  Any string that reaches the audit log
   could contain ``\\n``, allowing a caller to forge a plausible extra log
   line, or ANSI escapes that rewrite a terminal audit trail.  Account
   numbers and free-text fields are therefore restricted to a safe character
   whitelist -- the allow-list approach, never a deny-list.
3. **Identity/credential confusion.**  ``bool`` is a subclass of ``int`` in
   Python, so ``True`` would be accepted as the amount ``1``.  It is rejected
   explicitly.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# Exception hierarchy
#
# A purpose-built hierarchy lets a caller distinguish "your input is wrong"
# (the caller's fault, recoverable, safe to echo back to the client) from
# "the model is unavailable" (an infrastructure fault, not the client's
# problem, must not leak detail).  Catching a bare Exception forces the
# caller to handle both identically, which is itself a security weakness.
# ---------------------------------------------------------------------------


class RiskEngineError(Exception):
    """Base class for every error raised by the risk engine."""


class ValidationError(RiskEngineError, ValueError):
    """Raised when untrusted input fails validation."""


class FeatureStoreError(RiskEngineError):
    """Raised for feature-registry problems (unknown or duplicate feature)."""


class ModelNotFoundError(RiskEngineError):
    """Raised when a requested model version is not registered."""


class ModelLifecycleError(RiskEngineError):
    """Raised on an illegal model stage transition."""


class ProviderUnavailableError(RiskEngineError):
    """Raised when an AI/scoring backend cannot be reached or misbehaves."""


# ---------------------------------------------------------------------------
# Allow-list patterns
# ---------------------------------------------------------------------------

# Deliberately narrow: letters, digits and hyphens only.  A UUID4 satisfies
# this, and so do the legacy human-readable account numbers.  No whitespace,
# no newlines, no shell metacharacters, no Unicode direction-override
# characters that could disguise text in a report.
_ACCOUNT_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_TXN_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

MAX_AMOUNT = 1_000_000_000.0
"""Upper bound on a single transaction.

A sanity ceiling, not a security control on its own: it stops a poisoned
upstream record from producing arithmetic that overflows downstream
aggregations, and it gives the anomaly feature a meaningful denominator.
"""


# ---------------------------------------------------------------------------
# Scalar validators
# ---------------------------------------------------------------------------


def validate_amount(amount: Any, allow_zero: bool = False) -> float:
    """Return *amount* as a float, or raise :class:`ValidationError`.

    Rejects ``bool``, non-numeric types, non-finite floats, and amounts
    outside ``(0, MAX_AMOUNT]`` (or ``[0, MAX_AMOUNT]`` when *allow_zero*).
    """
    if isinstance(amount, bool):
        # bool subclasses int; accept it and True becomes the amount 1.
        raise ValidationError(f"amount must be numeric, got {type(amount).__name__}")
    if not isinstance(amount, (int, float)):
        raise ValidationError(f"amount must be numeric, got {type(amount).__name__}")
    value = float(amount)
    if not math.isfinite(value):
        # NaN would pass every `<` and `>` comparison; inf would break
        # every threshold rule silently.
        raise ValidationError(f"amount must be a finite number, got {value!r}")
    if allow_zero:
        if value < 0:
            raise ValidationError("amount cannot be negative")
    elif value <= 0:
        raise ValidationError(f"amount must be strictly positive, got {value}")
    if value > MAX_AMOUNT:
        raise ValidationError(f"amount exceeds the permitted ceiling of {MAX_AMOUNT}")
    return value


def validate_account_number(value: Any) -> str:
    """Return a validated account number, or raise :class:`ValidationError`."""
    if not isinstance(value, str):
        raise ValidationError(
            f"account_number must be a string, got {type(value).__name__}"
        )
    if not _ACCOUNT_NUMBER_RE.match(value):
        # Note the message deliberately does not echo the rejected value:
        # echoing untrusted input back is how log injection starts.
        raise ValidationError("account_number has an invalid format")
    return value


def validate_identifier(value: Any, field: str = "identifier") -> str:
    """Validate a machine-generated identifier such as a transaction id."""
    if not isinstance(value, str) or not _TXN_ID_RE.match(value):
        raise ValidationError(f"{field} has an invalid format")
    return value


def validate_country(value: Any) -> str:
    """Validate an ISO-3166 alpha-2 country code."""
    if not isinstance(value, str) or not _COUNTRY_RE.match(value):
        raise ValidationError("country must be a two-letter ISO-3166 code")
    return value


def validate_choice(value: Any, allowed: Iterable[str], field: str) -> str:
    """Validate *value* against a closed set of permitted strings.

    A closed set is the strongest possible validation: anything not on the
    list is rejected, so no unanticipated value can reach a branch that
    assumes one of the known cases.
    """
    allowed_set = frozenset(allowed)
    if not isinstance(value, str) or value not in allowed_set:
        raise ValidationError(f"{field} must be one of {sorted(allowed_set)}")
    return value


def validate_text(value: Any, field: str, max_length: int = 128) -> str:
    """Validate bounded free text that may reach a log or a report."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string, got {type(value).__name__}")
    if len(value) > max_length:
        raise ValidationError(f"{field} exceeds {max_length} characters")
    stripped = value.strip()
    if not stripped:
        raise ValidationError(f"{field} must not be empty")
    # Reject control characters (including CR/LF) and ANSI escape introducers.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in stripped):
        raise ValidationError(f"{field} contains control characters")
    return stripped


def validate_unit_interval(value: Any, field: str = "score") -> float:
    """Validate a normalised score in the inclusive range [0.0, 1.0]."""
    value = validate_amount(value, allow_zero=True)
    if value > 1.0:
        raise ValidationError(f"{field} must be normalised to [0, 1], got {value}")
    return value


def validate_weight_map(weights: Mapping[str, Any]) -> dict[str, float]:
    """Validate a ``{feature: weight}`` mapping and return a frozen copy."""
    if not isinstance(weights, Mapping) or not weights:
        raise ValidationError("weights must be a non-empty mapping")
    cleaned: dict[str, float] = {}
    for feature, weight in weights.items():
        if not isinstance(feature, str) or not feature:
            raise ValidationError("weight keys must be non-empty strings")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise ValidationError(f"weight for {feature} must be numeric")
        if not math.isfinite(float(weight)):
            raise ValidationError(f"weight for {feature} must be finite")
        cleaned[feature] = float(weight)
    return cleaned


# ---------------------------------------------------------------------------
# Redaction helpers
# ---------------------------------------------------------------------------

# Feature names whose values must never leave the process boundary in cleartext.
_SENSITIVE_KEY_MARKERS = ("account", "customer", "pan", "iban", "email", "phone")


def redact_account_number(value: str, keep_last: int = 4) -> str:
    """Mask all but the last *keep_last* characters of an account number."""
    if not isinstance(value, str) or len(value) <= keep_last:
        return "****"
    return "*" * (len(value) - keep_last) + value[-keep_last:]


def redact_mapping(
    payload: Mapping[str, Any],
    extra_sensitive: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a copy of *payload* with sensitive values masked.

    Returns a **new** dictionary: the caller's mapping is never mutated, so
    a redacted audit record cannot accidentally become the object the
    scoring logic later reads.  This is the security boundary the
    ``RedactingDecorator`` relies on before any payload is handed to an
    external model provider.
    """
    markers = tuple(_SENSITIVE_KEY_MARKERS) + tuple(extra_sensitive)
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        lowered = str(key).lower()
        if any(marker in lowered for marker in markers):
            redacted[key] = (
                redact_account_number(value) if isinstance(value, str) else "****"
            )
        else:
            redacted[key] = value
    return redacted
