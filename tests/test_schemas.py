"""Section 6, AI box 1: vision output is validated against a schema; invalid responses are never trusted."""
import json

import pytest
from pydantic import ValidationError

from app.domain.schemas import Category, ImageMetadata

VALID = {
    "subject": "red fox",
    "category": "animal",
    "attributes": ["orange fur", "wild", "forest"],
    "caption": "A red fox standing in a forest",
    "confidence": 0.94,
}


def test_brief_example_is_valid():
    meta = ImageMetadata.model_validate_json(json.dumps(VALID))
    assert meta.subject == "red fox"
    assert meta.category is Category.animal
    assert meta.confidence == 0.94


def _with(**changes):
    data = {**VALID, **changes}
    return json.dumps({k: v for k, v in data.items() if v is not ...})


INVALID_CASES = [
    (_with(caption=...), "missing required field"),
    (_with(confidence=1.7), "confidence above 1"),
    (_with(confidence=-0.1), "confidence below 0"),
    (_with(confidence="very sure"), "confidence not a number"),
    (_with(category="mammal"), "category outside the enum"),
    (_with(attributes=[]), "no attributes"),
    (_with(attributes=["x"] * 11), "too many attributes"),
    (_with(caption="fox"), "caption too short"),
    (_with(subject=""), "empty subject"),
    (json.dumps({**VALID, "species": "Vulpes vulpes"}), "unexpected extra field"),
    ("Sure! Here is the JSON you asked for: {", "prose instead of JSON"),
    ("", "empty response"),
]


@pytest.mark.parametrize("raw, why", INVALID_CASES, ids=[why for _, why in INVALID_CASES])
def test_invalid_vision_output_is_rejected(raw, why):
    with pytest.raises(ValidationError):
        ImageMetadata.model_validate_json(raw)


def test_whitespace_is_stripped():
    meta = ImageMetadata.model_validate_json(_with(subject="  red fox  "))
    assert meta.subject == "red fox"
