"""Core extraction logic for analog water-meter photos."""

from __future__ import annotations

import io
from typing import Any

from google.genai import types
from PIL import Image, ImageOps, UnidentifiedImageError

from config import (
    CONFIDENCE_THRESHOLD,
    GEMINI_MODEL,
    MAX_IMAGE_EDGE,
    MAX_UPLOAD_MB,
    get_client,
)
from schema import MeterExtraction, MeterModelOutput

PROMPT = """You are transcribing an analog mechanical water meter from a photo.

Read only what is visible and return the required JSON fields.

METER DIGIT STRIP
- Read the main rectangular odometer strip from left to right.
- `integer_part`: BLACK digits only, preserving leading zeros.
- `decimal_part`: RED digits only. It may contain one or more digits.
- Ignore all small circular pointer dials and printed multipliers.

HANDWRITTEN LABEL
- `handwritten_label`: the customer/connection label handwritten on the pipe,
  commonly in blue marker. Preserve the visible text. Use an empty string when
  there is no label.

QUALITY
- `confidence`: a number from 0 to 1 for the complete extraction. Reduce it for
  glare, blur, obstruction, a rolling digit, uncertain colour, or a cropped strip.
- `notes`: briefly identify every uncertainty. Otherwise use an empty string.

Never infer a plausible value. If any meter field is genuinely unreadable, return
an empty string for that field. Do not calculate the combined numeric reading; the
application derives it from the digit strings.
"""


def prepare_image(image_bytes: bytes) -> bytes:
    """Validate, orient, and resize an upload for consistent OCR input."""
    if not image_bytes:
        raise ValueError("The uploaded image is empty.")
    if len(image_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValueError(f"Image exceeds the {MAX_UPLOAD_MB} MB upload limit.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.verify()
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=95, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("The upload is not a valid PNG or JPEG image.") from exc


def extract(
    image_bytes: bytes,
    mime_type: str = "image/png",
    *,
    client: Any | None = None,
) -> MeterExtraction:
    """Extract and validate one meter reading.

    ``mime_type`` remains in the public API for backward compatibility; normalized
    images are always sent as JPEG. A client can be injected for offline tests.
    """
    del mime_type
    normalized = prepare_image(image_bytes)
    api_client = client or get_client()
    image_part = types.Part.from_bytes(data=normalized, mime_type="image/jpeg")

    response = api_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[PROMPT, image_part],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=MeterModelOutput,
            temperature=0,
        ),
    )

    parsed = response.parsed
    if parsed is None:
        parsed = MeterModelOutput.model_validate_json(response.text)
    else:
        parsed = MeterModelOutput.model_validate(parsed)
    return MeterExtraction.from_model(parsed, CONFIDENCE_THRESHOLD)
