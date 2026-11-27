"""The mismatch guard (brief section 4 part 3; section 6 S-1, S-2, S-3). Pure: no database, no network.

    evaluate(post, image, similarity, thresholds) -> GuardDecision

It combines the three signals the brief names:
  - tag validation:        the post's subject_key must equal the image's subject_key
  - similarity threshold:  cosine(post, image) >= similarity_min
  - confidence:            the image was tagged (not flagged/failed) with confidence >= confidence_min
plus one precondition: we must know what the post is about.

Every check runs (no short-circuit), so a rejection explains everything that failed.
ACCEPTED only when all checks pass.
"""
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from app.services.taxonomy import UNKNOWN
from app.services.thresholds import Thresholds

Verdict = Literal["ACCEPTED", "REJECTED"]
RULES = ("image_confidence", "post_subject_known", "subject_match", "similarity")


@dataclass(frozen=True)
class PostFacts:
    subject_key: str | None
    subject_reason: str | None = None      # Resolution.reason when the key is unknown


@dataclass(frozen=True)
class ImageFacts:
    status: str
    confidence: float | None
    subject: str | None                    # the vision model's own words, e.g. "gray wolf"
    subject_key: str | None
    flag_reasons: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class Check:
    rule: str
    passed: bool
    observed: Any
    threshold: Any
    reason: str | None                     # human-readable, only when the check failed


@dataclass(frozen=True)
class GuardDecision:
    verdict: Verdict
    reasons: list[str]
    checks: list[Check]

    def checks_as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.checks]


def _known(key: str | None) -> bool:
    return bool(key) and key != UNKNOWN


def check_image_confidence(image: ImageFacts, t: Thresholds) -> Check:
    observed = {"status": image.status, "confidence": image.confidence}
    threshold = t.confidence_min
    if image.status == "flagged":
        why = "; ".join(image.flag_reasons) or "flagged for review"
        return Check("image_confidence", False, observed, threshold,
                     f"Image classification is not trusted, flagged for human review: {why}")
    if image.status != "tagged" or image.confidence is None:
        return Check("image_confidence", False, observed, threshold,
                     f"Image has not been classified successfully (status: {image.status})")
    if image.confidence < t.confidence_min:
        return Check("image_confidence", False, observed, threshold,
                     f"Image classification is low-confidence ({image.confidence:.2f} < {t.confidence_min:.2f})")
    return Check("image_confidence", True, observed, threshold, None)


def check_post_subject_known(post: PostFacts) -> Check:
    if _known(post.subject_key):
        return Check("post_subject_known", True, post.subject_key, "a known subject", None)
    detail = f" ({post.subject_reason})" if post.subject_reason else ""
    return Check("post_subject_known", False, post.subject_key or UNKNOWN, "a known subject",
                 f"Could not tell what the post is about{detail}")


def check_subject_match(post: PostFacts, image: ImageFacts) -> Check:
    observed = {"expected": post.subject_key or UNKNOWN, "detected": image.subject_key or UNKNOWN,
                "image_subject": image.subject}
    if not _known(post.subject_key):
        return Check("subject_match", False, observed, "same subject",
                     "Subject cannot be confirmed: the post's subject is unknown")
    if not _known(image.subject_key):
        seen = f"'{image.subject}'" if image.subject else "nothing recognisable"
        return Check("subject_match", False, observed, "same subject",
                     f"Subject mismatch: expected {post.subject_key}, detected {seen}, "
                     "which is not a known category")
    if post.subject_key != image.subject_key:
        # The exact wording from the brief (section 4 part 3).
        return Check("subject_match", False, observed, "same subject",
                     f"Animal category mismatch: expected {post.subject_key}, detected {image.subject_key}")
    return Check("subject_match", True, observed, "same subject", None)


def check_similarity(similarity: float, t: Thresholds) -> Check:
    observed = round(similarity, 4)
    if similarity < t.similarity_min:
        return Check("similarity", False, observed, t.similarity_min,
                     f"Semantic similarity {similarity:.3f} is below the threshold {t.similarity_min:.2f}")
    return Check("similarity", True, observed, t.similarity_min, None)


def evaluate(post: PostFacts, image: ImageFacts, similarity: float, t: Thresholds) -> GuardDecision:
    checks = [
        check_image_confidence(image, t),
        check_post_subject_known(post),
        check_subject_match(post, image),
        check_similarity(similarity, t),
    ]
    reasons = [c.reason for c in checks if not c.passed and c.reason]
    return GuardDecision("ACCEPTED" if all(c.passed for c in checks) else "REJECTED", reasons, checks)


def no_match_reasons(post: PostFacts, candidates: Sequence[tuple[ImageFacts, float, GuardDecision]],
                     t: Thresholds) -> list[str]:
    """Post-level explanation when no candidate is accepted (probe P4): similarity and/or subjects."""
    reasons: list[str] = []
    if not _known(post.subject_key):
        reasons.append(check_post_subject_known(post).reason or "Could not tell what the post is about")
    if not candidates:
        reasons.append("No processed images to compare with")
        return reasons
    best = max(sim for _, sim, _ in candidates)
    if best < t.similarity_min:
        reasons.append(f"Best similarity {best:.3f} is below the threshold {t.similarity_min:.2f}")
    if _known(post.subject_key):
        detected = [img.subject_key or UNKNOWN for img, _, _ in candidates]
        if post.subject_key not in detected:
            reasons.append(f"Top candidates' subjects {sorted(set(detected))} don't match expected "
                           f"{post.subject_key}")
    untrusted = sum(1 for _, _, d in candidates if not d.checks[0].passed)
    if untrusted:
        reasons.append(f"{untrusted} of the top {len(candidates)} candidates are flagged or unclassified")
    if not reasons:
        reasons.append("No candidate passed every guard check")
    return reasons
