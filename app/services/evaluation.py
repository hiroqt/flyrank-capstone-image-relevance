"""The labeled eval set -> top-1 precision (brief section 6 Q-1, section 15; probe P5).

Headline (brief glossary): top-1 precision = of the labeled posts (one correct image each), the
share whose FIRST suggested image is the labeled one. A refusal on such a post counts as a miss.

Also measured, so the threshold is chosen with numbers, not a feeling:
  class-level   general posts (the probe posts): answered with a photo of the right class?
  refusals      off-topic posts must get "no confident match"
  hard negs     a known class but a species we have no photo of: refused, or a same-class false accept
  cross-species a suggestion of the wrong animal (a wolf for a fox post) on ANY post
  baseline      the same counts for "similarity only" (top-ranked image if similarity >= threshold),
                to show what the rest of the guard adds
True classes come from data/images/manifest.csv and are read ONLY here.
The guard is pure, so the sweep ranks each post once and re-runs only the guard per threshold.
"""
import csv
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from sqlalchemy.orm import Session

from app.db.models import Image, Post
from app.repositories import posts as posts_repo
from app.services.matching import evaluate_ranked, first_accepted, ranked_images
from app.services.thresholds import Thresholds

KINDS = ("positive", "class", "negative", "hard_negative")


@dataclass(frozen=True)
class EvalSet:
    version: str
    positives: dict[str, str]        # slug -> the one correct image file
    class_level: dict[str, str]      # slug -> class whose photos are all correct
    negatives: list[str]             # off-topic: refusal is correct
    hard_negatives: dict[str, str]   # slug -> the post's class (no photo of its species exists)
    digest: str                      # sha256 of the labels file: results are tied to an exact label set

    def kind(self, slug: str) -> str:
        if slug in self.positives:
            return "positive"
        if slug in self.class_level:
            return "class"
        return "hard_negative" if slug in self.hard_negatives else "negative"

    def slugs(self) -> list[str]:
        return [*self.positives, *self.class_level, *self.negatives, *self.hard_negatives]


def load_eval_set(path: Path) -> EvalSet:
    raw = path.read_bytes()
    data = yaml.safe_load(raw) or {}
    positives = {str(k): str(v) for k, v in (data.get("positives") or {}).items()}
    if len(positives) < 10:
        raise ValueError(f"{path}: the brief asks for 10 or more labeled posts (found {len(positives)})")
    return EvalSet(
        version=str(data.get("version", "e1")), positives=positives,
        class_level={str(k): str(v) for k, v in (data.get("class_level") or {}).items()},
        negatives=[str(s) for s in (data.get("negatives") or [])],
        hard_negatives={str(k): str(v) for k, v in (data.get("hard_negatives") or {}).items()},
        digest=hashlib.sha256(raw).hexdigest()[:12],
    )


def load_true_classes(manifest: Path) -> dict[str, str]:
    with manifest.open(newline="") as fh:
        return {row["file"]: row["true_class"] for row in csv.DictReader(fh)}


@dataclass
class PostOutcome:
    slug: str
    kind: str
    post_subject: str | None          # the system's subject_key for the post
    expected: str | None              # labeled image file, or the class for class-level/hard negatives
    suggested: str | None             # first accepted image, or None = "no confident match"
    suggested_class: str | None
    suggested_similarity: float | None
    expected_rank: int | None         # rank of the labeled image by pure similarity (positives)
    correct: bool
    cross_species: bool               # a suggestion of the wrong animal
    reasons: list[str] = field(default_factory=list)
    baseline_suggested: str | None = None
    baseline_correct: bool = False
    baseline_cross_species: bool = False


@dataclass(frozen=True)
class Metrics:
    similarity_min: float
    positives: int
    correct: int
    top1_precision: float
    answered: int
    precision_when_answering: float | None
    coverage: float
    class_level: int
    class_level_correct: int
    negatives: int
    correct_refusals: int
    hard_negatives: int
    hard_negatives_refused: int
    cross_species_false_accepts: int
    baseline_top1_precision: float
    baseline_false_accepts: int       # negatives + hard negatives given a photo by similarity alone
    baseline_cross_species: int

    def headline(self) -> str:
        return f"{self.top1_precision:.1%} ({self.correct}/{self.positives})"


def _file(image: Image) -> str:
    return image.file_path.rsplit("/", 1)[-1]


Rankings = dict[str, tuple[Post, list[tuple[Image, float]]]]


def collect_rankings(session: Session, tenant_id: str, eval_set: EvalSet, model: str) -> Rankings:
    out: Rankings = {}
    for slug in eval_set.slugs():
        post = posts_repo.get_by_ref(session, tenant_id, slug)
        if post is None:
            raise ValueError(f"eval post '{slug}' not found in tenant '{tenant_id}' (run seed + process first)")
        out[slug] = (post, ranked_images(session, tenant_id, post, model))
    return out


def _judge(kind: str, expected: str | None, post_class: str | None, suggested: str | None,
           true_class: dict[str, str]) -> tuple[bool, bool]:
    """(correct, cross_species) for one suggestion (None = refused)."""
    got = true_class.get(suggested) if suggested else None
    cross = suggested is not None and (post_class is None or got != post_class)
    if kind == "positive":
        return suggested == expected, cross
    if kind == "class":
        return got == expected, cross
    return suggested is None, cross


def evaluate(rankings: Rankings, eval_set: EvalSet, thresholds: Thresholds,
             true_class: dict[str, str]) -> tuple[Metrics, list[PostOutcome]]:
    outcomes: list[PostOutcome] = []
    for slug, (post, ranked) in rankings.items():
        kind = eval_set.kind(slug)
        expected = {"positive": eval_set.positives, "class": eval_set.class_level,
                    "hard_negative": eval_set.hard_negatives}.get(kind, {}).get(slug)
        post_class = (true_class.get(expected) if kind == "positive" else
                      expected if kind in ("class", "hard_negative") else None)
        evaluated = evaluate_ranked(post, ranked, thresholds)
        chosen = first_accepted(evaluated)
        suggested = _file(chosen.image) if chosen else None
        correct, cross = _judge(kind, expected, post_class, suggested, true_class)
        # Similarity-only baseline: the top-ranked image, if similar enough. No other checks.
        baseline = _file(ranked[0][0]) if ranked and ranked[0][1] >= thresholds.similarity_min else None
        b_correct, b_cross = _judge(kind, expected, post_class, baseline, true_class)
        outcomes.append(PostOutcome(
            slug=slug, kind=kind, post_subject=post.subject_key, expected=expected, suggested=suggested,
            suggested_class=true_class.get(suggested) if suggested else None,
            suggested_similarity=round(chosen.similarity, 4) if chosen else None,
            expected_rank=next((c.rank for c in evaluated if _file(c.image) == expected), None),
            correct=correct, cross_species=cross,
            reasons=[] if chosen else (evaluated[0].decision.reasons if evaluated else ["no embedded images"]),
            baseline_suggested=baseline, baseline_correct=b_correct, baseline_cross_species=b_cross,
        ))

    by = {k: [o for o in outcomes if o.kind == k] for k in KINDS}
    pos = by["positive"]
    answered = [o for o in pos if o.suggested]
    correct = sum(o.correct for o in pos)
    metrics = Metrics(
        similarity_min=thresholds.similarity_min,
        positives=len(pos), correct=correct, top1_precision=round(correct / len(pos), 4),
        answered=len(answered),
        precision_when_answering=round(correct / len(answered), 4) if answered else None,
        coverage=round(len(answered) / len(pos), 4),
        class_level=len(by["class"]), class_level_correct=sum(o.correct for o in by["class"]),
        negatives=len(by["negative"]), correct_refusals=sum(o.correct for o in by["negative"]),
        hard_negatives=len(by["hard_negative"]), hard_negatives_refused=sum(o.correct for o in by["hard_negative"]),
        cross_species_false_accepts=sum(o.cross_species for o in outcomes),
        baseline_top1_precision=round(sum(o.baseline_correct for o in pos) / len(pos), 4),
        baseline_false_accepts=sum(1 for o in by["negative"] + by["hard_negative"] if o.baseline_suggested),
        baseline_cross_species=sum(o.baseline_cross_species for o in outcomes),
    )
    return metrics, outcomes


def sweep(rankings: Rankings, eval_set: EvalSet, base: Thresholds, true_class: dict[str, str],
          start: float = 0.30, stop: float = 0.90, step: float = 0.02) -> list[Metrics]:
    n = int(round((stop - start) / step))
    return [evaluate(rankings, eval_set, base.with_similarity(round(start + i * step, 2)), true_class)[0]
            for i in range(n + 1)]


def choose_threshold(rows: list[Metrics]) -> Metrics:
    """1. zero cross-species false accepts (the fox/wolf boundary);
    2. highest top-1 precision; 3. every class-level (general) post still answered correctly;
    4. most refusals (off-topic + hard negatives); 5. the middle of the remaining plateau."""
    def keep(candidates, key):
        best = max(key(r) for r in candidates)
        return [r for r in candidates if key(r) == best]

    rows = keep(rows, lambda r: -r.cross_species_false_accepts)
    rows = keep(rows, lambda r: r.top1_precision)
    rows = keep(rows, lambda r: r.class_level_correct)
    rows = keep(rows, lambda r: r.correct_refusals + r.hard_negatives_refused)
    return rows[len(rows) // 2]


def results_payload(metrics: Metrics, outcomes: list[PostOutcome], eval_set: EvalSet, thresholds: Thresholds,
                    *, source: str, tenant_id: str, embed_model: str, sweep_rows: list[Metrics] | None = None) -> dict:
    return {
        "headline": f"Top-1 precision: {metrics.headline()}",
        "eval_set": {"version": eval_set.version, "sha256_12": eval_set.digest,
                     "positives": len(eval_set.positives), "class_level": len(eval_set.class_level),
                     "negatives": len(eval_set.negatives), "hard_negatives": len(eval_set.hard_negatives)},
        "source": source, "tenant": tenant_id, "embed_model": embed_model,
        "thresholds": thresholds.public(),
        "metrics": asdict(metrics),
        "posts": [asdict(o) for o in outcomes],
        "sweep": [asdict(r) for r in sweep_rows] if sweep_rows else None,
    }
