"""Matching thresholds: config/guard.yaml plus VISION_MIN_CONFIDENCE from the environment."""
from dataclasses import dataclass, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Thresholds:
    guard_version: str
    similarity_min: float     # tau_sim: post-image cosine similarity
    confidence_min: float     # tau_conf: the image's vision confidence (VISION_MIN_CONFIDENCE)
    label_min: float          # tau_label: the best taxonomy label must score at least this
    label_margin: float       # delta: required lead of the best label over the next in-scope label

    def public(self) -> dict[str, float | str]:
        """What the API shows next to every decision."""
        return {
            "guard_version": self.guard_version,
            "similarity_min": self.similarity_min,
            "confidence_min": self.confidence_min,
            "label_min": self.label_min,
            "label_margin": self.label_margin,
        }

    def with_similarity(self, value: float) -> "Thresholds":
        return replace(self, similarity_min=value)


def load_thresholds(path: Path, confidence_min: float) -> Thresholds:
    data = yaml.safe_load(path.read_text()) or {}
    t = Thresholds(
        guard_version=str(data["guard_version"]),
        similarity_min=float(data["similarity_min"]),
        confidence_min=float(confidence_min),
        label_min=float(data["label_min"]),
        label_margin=float(data["label_margin"]),
    )
    for name in ("similarity_min", "confidence_min", "label_min", "label_margin"):
        if not 0.0 <= getattr(t, name) <= 1.0:
            raise ValueError(f"{path}: {name} must be between 0 and 1")
    return t


def current_thresholds(settings) -> Thresholds:
    """Thresholds for this process: config/guard.yaml + VISION_MIN_CONFIDENCE."""
    return load_thresholds(settings.resolve_path(settings.guard_file), settings.vision_min_confidence)
