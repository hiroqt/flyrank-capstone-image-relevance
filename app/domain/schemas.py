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
