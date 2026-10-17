"""Tests for the validation and redaction layer (the trust boundary).

These cases exist because every value entering the engine is untrusted: it
originates from a transaction feed, not from a programmer.  The tests assert
the *refusal* behaviour as carefully as the success path, since a validator
that accepts too much is the more dangerous failure.
"""

from __future__ import annotations

import pytest

from capstone.validation import (
    MAX_AMOUNT,
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


class TestAmount:
    @pytest.mark.parametrize("value", [0.01, 1, 1250.0, MAX_AMOUNT])
    def test_accepts_valid_amounts(self, value: float) -> None:
        assert validate_amount(value) == float(value)

    @pytest.mark.parametrize(
        "value",
        [
            0,  # zero is not a transaction
            -1.0,  # negative
            MAX_AMOUNT * 2,  # above the sanity ceiling
            float("nan"),  # NaN passes every comparison
            float("inf"),  # inf breaks every threshold rule
            "100",  # a string is not a number
            None,
            True,  # bool is not an amount, despite subclassing int
        ],
    )
    def test_rejects_invalid_amounts(self, value: object) -> None:
        with pytest.raises(ValidationError):
            validate_amount(value)

    def test_zero_permitted_only_when_explicitly_allowed(self) -> None:
        assert validate_amount(0, allow_zero=True) == 0.0


class TestAccountsAndIdentifiers:
    @pytest.mark.parametrize(
        "value", ["ACC-00000001", "12345678", "a", "A-1-B-2", "x" * 64]
    )
    def test_accepts_allow_listed_account_numbers(self, value: str) -> None:
        assert validate_account_number(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            " ACC-1",  # leading whitespace
            "ACC 1",  # embedded space
            "ACC-1;DROP",  # shell metacharacter
            "ACC\n1",  # newline: log-injection vector
            "ACC\u202e1",  # Unicode direction override
            "x" * 65,  # over length
            42,
        ],
    )
    def test_rejects_malformed_account_numbers(self, value: object) -> None:
        with pytest.raises(ValidationError):
            validate_account_number(value)

    def test_error_message_does_not_echo_rejected_value(self) -> None:
        """Untrusted input must not be reflected into a message or a log."""
        hostile = "<script>alert(1)</script>"
        with pytest.raises(ValidationError) as excinfo:
            validate_account_number(hostile)
        assert hostile not in str(excinfo.value)

    def test_identifier_and_country_validation(self) -> None:
        assert validate_identifier("txn-abc-123", "txn_id") == "txn-abc-123"
        assert validate_country("GB") == "GB"
        for bad in ("", "gb", "GBR", "G1"):
            with pytest.raises(ValidationError):
                validate_country(bad)


class TestClosedSetsAndText:
    def test_choice_rejects_anything_off_the_list(self) -> None:
        assert validate_choice("wire", {"wire", "pos"}, "channel") == "wire"
        with pytest.raises(ValidationError):
            validate_choice("telegraph", {"wire", "pos"}, "channel")

    def test_text_strips_and_bounds_input(self) -> None:
        assert validate_text("  hello  ", "field") == "hello"
        with pytest.raises(ValidationError):
            validate_text("", "field")
        with pytest.raises(ValidationError):
            validate_text("x" * 9, "field", max_length=8)
        with pytest.raises(ValidationError):
            validate_text("line1\nline2", "field")
        with pytest.raises(ValidationError):
            validate_text("bell\x07", "field")
        with pytest.raises(ValidationError):
            validate_text(3.14, "field")


class TestScoreAndWeights:
    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
    def test_unit_interval_accepts(self, value: float) -> None:
        assert validate_unit_interval(value) == value

    @pytest.mark.parametrize("value", [-0.1, 1.0001, float("nan")])
    def test_unit_interval_rejects(self, value: float) -> None:
        with pytest.raises(ValidationError):
            validate_unit_interval(value)

    def test_weight_map_returns_a_numeric_copy(self) -> None:
        cleaned = validate_weight_map({"a": 1, "b": 0.5})
        assert cleaned == {"a": 1.0, "b": 0.5}
        assert all(isinstance(v, float) for v in cleaned.values())

    @pytest.mark.parametrize(
        "weights",
        [{}, {"": 1.0}, {"a": "heavy"}, {"a": True}, {"a": float("inf")}, ["a"]],
    )
    def test_weight_map_rejects_malformed_input(self, weights: object) -> None:
        with pytest.raises(ValidationError):
            validate_weight_map(weights)  # type: ignore[arg-type]


class TestRedaction:
    def test_account_number_keeps_only_the_suffix(self) -> None:
        assert redact_account_number("ACC-00000001") == "********0001"
        assert redact_account_number("1234") == "****"
        assert redact_account_number("12") == "****"

    def test_redacted_mapping_is_a_copy_and_masks_sensitive_keys(self) -> None:
        original = {"account_number": "ACC-00000001", "amount": 12.5}
        redacted = redact_mapping(original)

        assert redacted is not original
        assert redacted["account_number"] == "********0001"
        assert redacted["amount"] == 12.5
        # The caller's object is untouched: a redacted view must never
        # become the object later read by scoring logic.
        assert original["account_number"] == "ACC-00000001"

    @pytest.mark.parametrize(
        "key", ["customer_id", "pan", "iban_ref", "email_address", "phone"]
    )
    def test_each_sensitive_marker_is_masked(self, key: str) -> None:
        # A nine-character value keeps only its last four characters visible,
        # so the marker matching is what is being asserted here, not the
        # length of the mask.
        masked = redact_mapping({key: "sensitive"})[key]
        assert masked == "*****tive"
        assert masked != "sensitive"

    def test_extra_sensitive_keys_can_be_declared_by_the_caller(self) -> None:
        redacted = redact_mapping({"nickname": "Jules"}, extra_sensitive=("nickname",))
        assert redacted["nickname"] == "*ules"

    def test_non_string_sensitive_values_are_masked_whole(self) -> None:
        assert redact_mapping({"account_balance": 987654.32})["account_balance"] == "****"
