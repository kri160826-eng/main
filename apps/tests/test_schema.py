from decimal import Decimal

import pytest
from pydantic import ValidationError

from schema import MeterExtraction, MeterModelOutput, combine_reading, reading_text


def output(**overrides: object) -> MeterModelOutput:
    values = {
        "integer_part": "00496",
        "decimal_part": "750",
        "handwritten_label": "305",
        "confidence": 0.95,
        "notes": "",
    }
    values.update(overrides)
    return MeterModelOutput(**values)


def test_combines_digits_without_losing_decimal_precision() -> None:
    result = MeterExtraction.from_model(output(), confidence_threshold=0.8)
    assert result.reading_m3 == Decimal("496.750")
    assert reading_text(result.reading_m3) == "496.750"
    assert result.review_required is False


def test_unreadable_integer_has_no_numeric_reading() -> None:
    result = MeterExtraction.from_model(
        output(integer_part="", confidence=0.3), confidence_threshold=0.8
    )
    assert result.reading_m3 is None
    assert result.review_required is True


def test_low_confidence_requires_review() -> None:
    result = MeterExtraction.from_model(output(confidence=0.79), confidence_threshold=0.8)
    assert result.review_required is True


def test_non_digit_meter_output_is_rejected() -> None:
    with pytest.raises(ValidationError):
        output(integer_part="00O96")


@pytest.mark.parametrize(
    ("integer_part", "decimal_part", "expected"),
    [("00012", "", Decimal("12")), ("00012", "05", Decimal("12.05")), ("", "5", None)],
)
def test_combine_reading(integer_part: str, decimal_part: str, expected: Decimal | None) -> None:
    assert combine_reading(integer_part, decimal_part) == expected
