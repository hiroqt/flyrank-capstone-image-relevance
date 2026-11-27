"""Subject classes and subject_key resolution (PLAN.md section 5). Pure: no database, no network.

subject_key answers "which known class is this about?" for both sides of a match. Both sides go
through the same function, from a short subject phrase:
  - image: the vision model's `subject`             ("gray wolf"     -> wolf)
  - post:  the topic extracted from the post's text ("Vulpes vulpes" -> red fox -> fox)

A phrase resolves to the nearest taxonomy label, and it is `unknown` when
  - the nearest label is an out-of-scope anchor ("coyote" -> other_canid, "domestic cat" -> cat), or
  - the best label scores below min_score, or
  - the best label does not lead the next *in-scope* label by `margin` (e.g. torn between fox and wolf).
This does not depend on the vision model's self-reported confidence.
"""
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

UNKNOWN = "unknown"


@dataclass(frozen=True)
class Taxonomy:
    version: str
    labels: dict[str, str]          # in scope: key -> description that gets embedded
    out_of_scope: dict[str, str]    # "none of the above" anchors

    def all_labels(self) -> dict[str, str]:
        return {**self.labels, **self.out_of_scope}


def current_taxonomy(settings) -> Taxonomy:
    return load_taxonomy(settings.resolve_path(settings.taxonomy_file))


def load_taxonomy(path: Path) -> Taxonomy:
    data = yaml.safe_load(path.read_text()) or {}
    labels = {str(k): str(v) for k, v in (data.get("labels") or {}).items()}
    out_of_scope = {str(k): str(v) for k, v in (data.get("out_of_scope") or {}).items()}
    if len(labels) < 2:
        raise ValueError(f"{path}: a taxonomy needs at least two labels")
    if UNKNOWN in labels or UNKNOWN in out_of_scope:
        raise ValueError(f"{path}: '{UNKNOWN}' is reserved")
    if set(labels) & set(out_of_scope):
        raise ValueError(f"{path}: a key cannot be both in scope and out of scope")
    return Taxonomy(version=str(data.get("version", "t1")), labels=labels, out_of_scope=out_of_scope)


@dataclass(frozen=True)
class Resolution:
    key: str                       # an in-scope label key, or "unknown"
    best: str | None               # nearest label overall (may be out of scope)
    best_score: float | None
    runner_up: str | None          # next-best in-scope label (the margin is measured against it)
    runner_up_score: float | None
    reason: str | None             # why the key is unknown (None when resolved)

    def detail(self) -> dict:
        return asdict(self)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector size mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def resolve(vector: Sequence[float], label_vectors: Mapping[str, Sequence[float]], *, in_scope: set[str],
            min_score: float, margin: float) -> Resolution:
    if not set(label_vectors) & in_scope:
        return Resolution(UNKNOWN, None, None, None, None, "no taxonomy labels are embedded yet")
    scored = sorted(((cosine(vector, v), key) for key, v in label_vectors.items()), reverse=True)
    best_score, best = scored[0]
    others_in_scope = [(s, k) for s, k in scored if k in in_scope and k != best]
    runner_up_score, runner_up = others_in_scope[0] if others_in_scope else (None, None)

    reason = None
    if best not in in_scope:
        reason = f"closest category is '{best}' ({best_score:.2f}), which is outside the taxonomy"
    elif best_score < min_score:
        reason = f"best label '{best}' at {best_score:.2f} < {min_score:.2f}"
    elif runner_up_score is not None and best_score - runner_up_score < margin:
        reason = (f"'{best}' {best_score:.2f} is not clearly ahead of '{runner_up}' {runner_up_score:.2f} "
                  f"(lead {best_score - runner_up_score:.3f} < {margin:.2f})")
    return Resolution(
        key=UNKNOWN if reason else best,
        best=best, best_score=round(best_score, 4),
        runner_up=runner_up, runner_up_score=None if runner_up_score is None else round(runner_up_score, 4),
        reason=reason,
    )


@dataclass(frozen=True)
class SubjectResolver:
    """Label vectors + thresholds, ready to resolve a subject phrase's vector (images and posts alike)."""

    label_vectors: dict[str, list[float]]
    in_scope: frozenset[str]
    min_score: float
    margin: float
    version: str = ""  # resolver_version(): stored with each resolution so a config change re-resolves

    def resolve(self, vector: Sequence[float], *, category: str | None = None,
                subject: str | None = None) -> Resolution:
        res = resolve(vector, self.label_vectors, in_scope=set(self.in_scope), min_score=self.min_score,
                      margin=self.margin)
        if category is not None and category != "animal":
            # Every in-scope class is an animal: a non-animal subject is never one of them.
            return Resolution(UNKNOWN, res.best, res.best_score, res.runner_up, res.runner_up_score,
                              f"'{subject}' is {category}, not an animal in the taxonomy")
        return res


def resolver_version(taxonomy: Taxonomy, label_min: float, label_margin: float) -> str:
    """Identifies the rules a subject_key was resolved with. Changes when the taxonomy version, any label
    text (even without a version bump) or the thresholds change, so stored keys are re-resolved."""
    labels = hashlib.sha256(json.dumps(taxonomy.all_labels(), sort_keys=True).encode()).hexdigest()[:8]
    return f"{taxonomy.version}-{labels}:{label_min}:{label_margin}"
