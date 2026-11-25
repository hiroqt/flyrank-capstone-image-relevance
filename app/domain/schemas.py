"""The vision output contract (brief section 4, part 1).

Every vision response is parsed with ImageMetadata.model_validate_json(). Anything that
fails validation is never trusted: it is retried or flagged, never silently accepted.
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Category(str, Enum):
    animal = "animal"
    plant = "plant"
    landscape = "landscape"
    person = "person"
    food = "food"
    object = "object"
    other = "other"


class ImageMetadata(BaseModel):
    """Exactly the five fields from the brief. extra="forbid" rejects anything else."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    subject: str = Field(min_length=2, max_length=60, examples=["red fox"])
    category: Category = Field(examples=["animal"])
    attributes: list[str] = Field(min_length=1, max_length=10, examples=[["orange fur", "wild", "forest"]])
    caption: str = Field(min_length=10, max_length=200, examples=["A red fox standing in a forest"])
    confidence: float = Field(ge=0.0, le=1.0, examples=[0.94])


# The same contract as a provider-neutral JSON Schema, sent to the model as structured-output
# instructions (Gemini `response_json_schema`, Ollama `format`). It is a request to the model,
# not a guarantee: every response is still validated with ImageMetadata above.
# tests/test_schemas.py keeps the two definitions in sync.
VISION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Most specific common name of the main subject, e.g. 'red fox'"},
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "attributes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10},
        "caption": {"type": "string", "description": "One factual sentence describing the image"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["subject", "category", "attributes", "caption", "confidence"],
    "additionalProperties": False,
}
