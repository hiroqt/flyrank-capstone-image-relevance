"""The mismatch guard as a pure module (brief section 1: "build the guard as its own small module first").
Hand-written facts, no database, no AI. Section 6 S-1, S-2, S-3."""
from app.services.guard import ImageFacts, PostFacts, evaluate, no_match_reasons
from app.services.taxonomy import UNKNOWN, SubjectResolver, Taxonomy, resolve
from app.services.thresholds import Thresholds

T = Thresholds(guard_version="g-test", similarity_min=0.78, confidence_min=0.70, label_min=0.60, label_margin=0.02)
FOX_POST = PostFacts(subject_key="fox")
RED_FOX = ImageFacts(status="tagged", confidence=0.95, subject="red fox", subject_key="fox")
GRAY_WOLF = ImageFacts(status="tagged", confidence=0.85, subject="gray wolf", subject_key="wolf")


def test_the_brief_scenario_wolf_on_a_fox_post_is_rejected_with_the_exact_reason():
    decision = evaluate(FOX_POST, GRAY_WOLF, similarity=0.80, t=T)  # similar enough, confident: still wrong
    assert decision.verdict == "REJECTED"
    assert decision.reasons == ["Animal category mismatch: expected fox, detected wolf"]


def test_the_right_animal_with_enough_similarity_is_accepted():
    decision = evaluate(FOX_POST, RED_FOX, similarity=0.83, t=T)
    assert (decision.verdict, decision.reasons) == ("ACCEPTED", [])
    assert all(c.passed for c in decision.checks)


def test_every_check_runs_so_the_explanation_lists_every_failure():
    flagged_wolf = ImageFacts(status="flagged", confidence=0.55, subject="gray wolf", subject_key="wolf",
                              flag_reasons=["Low confidence 0.55 < 0.70"])
    decision = evaluate(FOX_POST, flagged_wolf, similarity=0.41, t=T)
    assert [c.rule for c in decision.checks] == ["image_confidence", "post_subject_known", "subject_match", "similarity"]
    assert [c.passed for c in decision.checks] == [False, True, False, False]
    assert decision.reasons == [
        "Image classification is not trusted, flagged for human review: Low confidence 0.55 < 0.70",
        "Animal category mismatch: expected fox, detected wolf",
        "Semantic similarity 0.410 is below the threshold 0.78",
    ]


def test_each_check_records_what_it_saw_and_the_threshold():
    sim = evaluate(FOX_POST, RED_FOX, similarity=0.7712, t=T).checks[3]
    assert (sim.rule, sim.passed, sim.observed, sim.threshold) == ("similarity", False, 0.7712, 0.78)


def test_a_flagged_image_is_never_accepted_even_for_the_right_post():
    flagged_fox = ImageFacts(status="flagged", confidence=0.6, subject="red fox", subject_key="fox",
                             flag_reasons=["Low confidence 0.60 < 0.70"])
    assert evaluate(FOX_POST, flagged_fox, similarity=0.95, t=T).verdict == "REJECTED"


def test_an_unknown_post_subject_is_refused_with_its_reason():
    post = PostFacts(subject_key=UNKNOWN, subject_reason="closest category is 'bird' (0.80), which is outside "
                                                         "the taxonomy")
    decision = evaluate(post, RED_FOX, similarity=0.90, t=T)
    assert decision.verdict == "REJECTED"
    assert decision.reasons[0] == ("Could not tell what the post is about (closest category is 'bird' (0.80), "
                                   "which is outside the taxonomy)")


def test_an_image_whose_subject_is_not_a_known_category_is_refused():
    coyote = ImageFacts(status="tagged", confidence=0.9, subject="coyote", subject_key=UNKNOWN)
    reasons = evaluate(FOX_POST, coyote, similarity=0.85, t=T).reasons
    assert reasons == ["Subject mismatch: expected fox, detected 'coyote', which is not a known category"]


def test_no_match_summary_gives_similarity_and_subject_reasons():
    candidates = [(GRAY_WOLF, 0.70, evaluate(FOX_POST, GRAY_WOLF, 0.70, T)),
                  (ImageFacts("tagged", 0.9, "golden retriever", "dog"), 0.66,
                   evaluate(FOX_POST, ImageFacts("tagged", 0.9, "golden retriever", "dog"), 0.66, T))]
    assert no_match_reasons(FOX_POST, candidates, T) == [
        "Best similarity 0.700 is below the threshold 0.78",
        "Top candidates' subjects ['dog', 'wolf'] don't match expected fox",
    ]


# ---- subject_key resolution (pure) ---------------------------------------------------------------
LABELS = {"fox": [1.0, 0.0, 0.0, 0.0], "wolf": [0.0, 1.0, 0.0, 0.0], "cat": [0.0, 0.0, 1.0, 0.0]}
IN_SCOPE = {"fox", "wolf"}


def test_resolve_picks_the_nearest_in_scope_label():
    r = resolve([0.9, 0.1, 0.0, 0.0], LABELS, in_scope=IN_SCOPE, min_score=0.6, margin=0.02)
    assert (r.key, r.best, r.runner_up, r.reason) == ("fox", "fox", "wolf", None)


def test_resolve_is_unknown_when_torn_between_two_classes():
    r = resolve([0.7, 0.69, 0.0, 0.0], LABELS, in_scope=IN_SCOPE, min_score=0.6, margin=0.02)
    assert r.key == UNKNOWN and "not clearly ahead" in r.reason


def test_resolve_is_unknown_when_the_nearest_label_is_out_of_scope():
    r = resolve([0.2, 0.1, 0.95, 0.0], LABELS, in_scope=IN_SCOPE, min_score=0.6, margin=0.02)
    assert (r.key, r.best) == (UNKNOWN, "cat")
    assert "outside the taxonomy" in r.reason


def test_resolve_is_unknown_when_nothing_is_close():
    r = resolve([0.0, 0.0, 0.0, 1.0], LABELS, in_scope=IN_SCOPE, min_score=0.6, margin=0.02)
    assert r.key == UNKNOWN


def test_a_non_animal_subject_never_resolves_to_an_animal_class():
    resolver = SubjectResolver(LABELS, frozenset(IN_SCOPE), 0.6, 0.02)
    r = resolver.resolve([1.0, 0.0, 0.0, 0.0], category="food", subject="fox-shaped cookie")
    assert r.key == UNKNOWN and r.reason == "'fox-shaped cookie' is food, not an animal in the taxonomy"


def test_taxonomy_rejects_a_key_that_is_both_in_and_out_of_scope(tmp_path):
    import pytest

    from app.services.taxonomy import load_taxonomy

    path = tmp_path / "t.yaml"
    path.write_text("labels: {fox: a, wolf: b}\nout_of_scope: {fox: c}\n")
    with pytest.raises(ValueError):
        load_taxonomy(path)
    assert isinstance(Taxonomy("v", {"a": "x"}, {}), Taxonomy)
