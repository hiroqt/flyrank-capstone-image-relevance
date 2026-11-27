"""Matching, the guard, review and the eval on REAL recorded Gemini outputs (eval/snapshot.json).

The snapshot holds the vision tags, post topics and every vector from the real run, so these tests
check the brief's acceptance probes against real embeddings while running offline, with no key.
Section 6 M-1, M-2, S-1, S-2, S-3, B-2, Q-1; probes P2, P3, P4, P5.
"""
import json
from pathlib import Path

import pytest

from app.cli import EVAL_TENANT, LABELS_FILE, MANIFEST_FILE, RESULTS_FILE, SNAPSHOT_FILE
from app.config import get_settings
from app.db.session import get_sessionmaker
from app.services import evaluation, snapshot
from app.services.taxonomy import current_taxonomy
from app.services.thresholds import current_thresholds

ROOT = Path(__file__).resolve().parents[1]
H = {"X-Tenant-Id": EVAL_TENANT}
FOX_POST = "the-behavior-of-red-foxes"


@pytest.fixture
def real():
    settings = get_settings()
    data = snapshot.read(ROOT / SNAPSHOT_FILE)
    with get_sessionmaker()() as session, session.begin():
        snapshot.load(session, EVAL_TENANT, data, taxonomy=current_taxonomy(settings),
                      thresholds=current_thresholds(settings), settings=settings)
    return data


def _image_id(client, file: str) -> str:
    items = client.get("/images", params={"limit": 200}, headers=H).json()["items"]
    return next(i["id"] for i in items if i["file_path"].endswith(file))


def test_p2_fox_post_ranks_a_fox_first_and_wolf_and_dog_clearly_lower(client, real):
    body = client.get(f"/posts/{FOX_POST}/images", headers=H).json()
    assert (body["status"], body["post_subject"]) == ("match", "fox")
    assert body["suggestion"]["subject_key"] == "fox" and body["suggestion"]["rank"] == 1
    best = {s["subject_key"]: s for s in body["by_subject"]}
    assert best["fox"]["best_rank"] == 1
    assert best["wolf"]["best_rank"] > 5 and best["dog"]["best_rank"] > best["wolf"]["best_rank"]
    assert best["fox"]["best_similarity"] - best["wolf"]["best_similarity"] > 0.03


def test_p3_the_forced_wolf_is_rejected_with_a_category_mismatch(client, real):
    wolf = _image_id(client, "img_351e.jpg")  # the wolf closest to the fox post
    body = client.post(f"/posts/{FOX_POST}/candidates", json={"image_id": wolf}, headers=H).json()
    assert body["verdict"] == "REJECTED"
    assert body["reasons"] == ["Animal category mismatch: expected fox, detected wolf"]
    checks = {c["rule"]: c["passed"] for c in body["checks"]}
    assert checks == {"image_confidence": True, "post_subject_known": True, "subject_match": False,
                      "similarity": True}  # similarity alone would have let this wolf through


def test_p4_a_post_with_no_suitable_image_gets_no_confident_match_with_reasons(client, real):
    body = client.get("/posts/emperor-penguins-huddle/images", headers=H).json()
    assert (body["status"], body["suggestion"]) == ("no_confident_match", None)
    assert any("Could not tell what the post is about" in r for r in body["reasons"])
    assert any(r.startswith("Best similarity") and "below the threshold" in r for r in body["reasons"])
    assert all(c["verdict"] == "REJECTED" for c in body["candidates"])


def test_m2_vulpes_vulpes_matches_a_fox_without_the_word_fox(client, real):
    post = client.get("/posts/vulpes-vulpes-in-european-cities", headers=H).json()
    assert "fox" not in (post["title"] + post["body"]).lower()
    body = client.get("/posts/vulpes-vulpes-in-european-cities/images", headers=H).json()
    assert body["status"] == "match" and body["post_subject"] == "fox"
    assert body["candidates"][0]["subject_key"] == "fox"          # ranked first by meaning alone
    assert body["suggestion"]["subject"] == "red fox"


def test_p5_eval_reproduces_the_committed_results_and_the_readme_number(real):
    settings = get_settings()
    eval_set = evaluation.load_eval_set(ROOT / LABELS_FILE)
    with get_sessionmaker()() as session:
        rankings = evaluation.collect_rankings(session, EVAL_TENANT, eval_set, real["embed_model"])
        metrics, _ = evaluation.evaluate(rankings, eval_set, current_thresholds(settings),
                                         evaluation.load_true_classes(ROOT / MANIFEST_FILE))
    committed = json.loads((ROOT / RESULTS_FILE).read_text())
    assert committed["eval_set"]["sha256_12"] == eval_set.digest, "labels changed: re-run python -m app.cli eval"
    assert f"Top-1 precision: {metrics.headline()}" == committed["headline"]
    assert committed["headline"] in (ROOT / "README.md").read_text(), "README must show the eval's number"
    assert metrics.cross_species_false_accepts == 0


def test_review_approve_reject_explain_and_conflicts(client, real):
    client.get(f"/posts/{FOX_POST}/images", headers=H)
    wolf = _image_id(client, "img_351e.jpg")
    rejected = client.post(f"/posts/{FOX_POST}/candidates", json={"image_id": wolf}, headers=H).json()
    queue = client.get("/suggestions", params={"verdict": "accepted", "review": "pending"}, headers=H).json()
    accepted_id = queue["items"][0]["id"]

    explain = client.get(f"/suggestions/{rejected['suggestion_id']}/explain", headers=H).json()
    assert explain["summary"] == "Refused: Animal category mismatch: expected fox, detected wolf"
    assert {c["rule"] for c in explain["checks"]} == {"image_confidence", "post_subject_known", "subject_match",
                                                      "similarity"}

    ok = client.post(f"/suggestions/{accepted_id}/approve", json={"reviewer": "editor", "note": "great shot"},
                     headers={**H, "Idempotency-Key": "approve-1"})
    assert ok.status_code == 200 and ok.json()["review"]["decision"] == "approved"
    assert ok.json()["overrides_guard"] is False
    replay = client.post(f"/suggestions/{accepted_id}/approve", json={"reviewer": "editor", "note": "great shot"},
                         headers={**H, "Idempotency-Key": "approve-1"})
    assert replay.headers.get("Idempotency-Replayed") == "true" and replay.json() == ok.json()
    assert client.post(f"/suggestions/{accepted_id}/approve", headers=H).status_code == 200   # same decision: no-op

    conflict = client.post(f"/suggestions/{accepted_id}/reject", headers=H)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "conflict"

    override = client.post(f"/suggestions/{rejected['suggestion_id']}/reject", headers=H).json()
    assert override["overrides_guard"] is False and override["verdict"] == "REJECTED"
    assert client.get("/suggestions", params={"review": "approved"}, headers=H).json()["total"] == 1
    assert "img_351e.jpg" in client.get("/admin/review", headers=H).text


def test_snapshot_refuses_to_load_against_a_changed_taxonomy(real):
    from app.services.taxonomy import Taxonomy

    changed = Taxonomy("t-changed", {"fox": "a different description", "wolf": "x"}, {})
    settings = get_settings()
    with pytest.raises(snapshot.SnapshotError), get_sessionmaker()() as session:
        snapshot.load(session, "eval-2", real, taxonomy=changed, thresholds=current_thresholds(settings),
                      settings=settings)
