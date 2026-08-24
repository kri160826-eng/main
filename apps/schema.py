"""Validated models for a single water-meter extraction."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field, field_validator


class MeterModelOutput(BaseModel):
    """Fields Gemini must read directly from the image."""

    integer_part: str = Field(
        description="Black odometer digits, including leading zeros; empty if unreadable."
    )
    decimal_part: str = Field(
        description="Red odometer digits; empty if unreadable or absent."
    )
    handwritten_label: str = Field(
        description="Handwritten customer/connection label; empty if not visible."
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description="Confidence in the complete extraction, from 0 to 1.",
    )
    notes: str = Field(
        default="",
        description="Short explanation of glare, blur, ambiguity, or unreadable fields.",
    )

    @field_validator("integer_part", "decimal_part")
    @classmethod
    def validate_meter_digits(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and not cleaned.isascii():
            raise ValueError("meter digits must use ASCII numerals")
        if cleaned and not cleaned.isdigit():
            raise ValueError("meter fields may contain digits only")
        return cleaned

    @field_validator("handwritten_label", "notes")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class MeterExtraction(MeterModelOutput):
    """Application result with a code-derived reading and review decision."""

    reading_m3: Decimal | None
    review_required: bool

    @classmethod
    def from_model(
        cls,
        output: MeterModelOutput,
        confidence_threshold: float,
    ) -> "MeterExtraction":
        reading = combine_reading(output.integer_part, output.decimal_part)
        return cls(
            **output.model_dump(),
            reading_m3=reading,
            review_required=reading is None or output.confidence < confidence_threshold,
        )


def combine_reading(integer_part: str, decimal_part: str) -> Decimal | None:
    """Combine validated meter digits while preserving decimal precision."""
    if not integer_part or not integer_part.isascii() or not integer_part.isdigit():
        return None
    if decimal_part and (not decimal_part.isascii() or not decimal_part.isdigit()):
        return None
    text = str(int(integer_part))
    if decimal_part:
        text = f"{text}.{decimal_part}"
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def reading_text(reading: Decimal | None) -> str:
    """Return a CSV/UI-safe reading without scientific notation."""
    return "" if reading is None else format(reading, "f")
