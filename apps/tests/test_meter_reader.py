import io
from types import SimpleNamespace

from PIL import Image

from meter_reader import extract, prepare_image


def image_bytes() -> bytes:
    image = Image.new("RGB", (3000, 1000), color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeModels:
    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(
            parsed={
                "integer_part": "00496",
                "decimal_part": "750",
                "handwritten_label": "305",
                "confidence": 0.95,
                "notes": "",
            },
            text="",
        )


def test_prepare_image_resizes_and_converts_to_jpeg() -> None:
    normalized = prepare_image(image_bytes())
    with Image.open(io.BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 2400


def test_extract_uses_model_fields_and_derives_reading() -> None:
    models = FakeModels()
    result = extract(image_bytes(), client=SimpleNamespace(models=models))
    assert str(result.reading_m3) == "496.750"
    assert result.handwritten_label == "305"
    assert models.kwargs["model"]
