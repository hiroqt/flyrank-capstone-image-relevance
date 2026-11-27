"""Command-line entry points: python -m app.cli <command>

  seed      create demo tenants, register every image in data/images (sha256 dedupe) and load
            data/posts.yaml. Idempotent: safe to run again.
  process   enqueue the batch jobs (images: vision + embeddings; posts: topic + embeddings);
            --wait polls until the worker finishes them
  snapshot  record this tenant's AI outputs (vision, topics, vectors) to eval/snapshot.json
  eval      top-1 precision on eval/labels.yaml (brief section 6 Q-1, probe P5). By default it replays
            eval/snapshot.json into the isolated 'eval' tenant: no key, no cost, exact README number.
            --tenant demo evaluates live data instead; --sweep tunes similarity_min (--apply saves it)
"""
import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import yaml

from app.config import PROJECT_ROOT, get_settings
from app.db.session import get_sessionmaker
from app.logging_config import configure_logging
from app.repositories import images as images_repo
from app.repositories import jobs as jobs_repo
from app.repositories import posts as posts_repo
from app.repositories import tenants
from app.services.jobs import embed_model_name, enqueue, vision_model_name

log = logging.getLogger("app.cli")

DEMO_TENANTS = {
    "demo": "Demo tenant (all probes run here)",
    "other": "Second tenant, used to prove tenant isolation",
}
EVAL_TENANT = "eval"
SNAPSHOT_FILE = "eval/snapshot.json"
LABELS_FILE = "eval/labels.yaml"
RESULTS_FILE = "eval/results.json"
MANIFEST_FILE = "data/images/manifest.csv"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def seed(images_dir: Path | None = None, tenant_id: str = "demo", posts_file: Path | None = None) -> tuple[int, int]:
    with get_sessionmaker()() as session, session.begin():
        for tid, name in DEMO_TENANTS.items():
            tenants.upsert(session, tid, name)
    log.info("Seeded tenants: %s", ", ".join(DEMO_TENANTS))
    if posts_file is not None:
        load_posts(posts_file, tenant_id)
    if images_dir is None:
        return 0, 0
    return register_images(images_dir, tenant_id)


def load_posts(path: Path, tenant_id: str) -> int:
    if not path.exists():
        log.warning("Posts file %s does not exist; no posts loaded", path)
        return 0
    entries = (yaml.safe_load(path.read_text()) or {}).get("posts") or []
    added = 0
    with get_sessionmaker()() as session, session.begin():
        for entry in entries:
            added += posts_repo.upsert(session, tenant_id, slug=entry["slug"], title=entry["title"],
                                       body=" ".join(entry["body"].split()))
    log.info("Loaded %d post(s) for tenant '%s' (%d new) from %s", len(entries), tenant_id, added, path)
    return added


def register_images(images_dir: Path, tenant_id: str) -> tuple[int, int]:
    if not images_dir.is_dir():
        log.warning("Images folder %s does not exist; nothing registered", images_dir)
        return 0, 0
    manifest = _read_manifest(images_dir / "manifest.csv")
    files = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    added = skipped = 0
    with get_sessionmaker()() as session, session.begin():
        for path in files:
            row = manifest.get(path.name, {})
            created = images_repo.register(
                session, tenant_id,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                file_path=_stored_path(path),
                source_url=row.get("source_url") or None,
                license=row.get("license") or None,
                photographer=row.get("photographer") or None,
            )
            added, skipped = (added + 1, skipped) if created else (added, skipped + 1)
    log.info("Registered %d new image(s) for tenant '%s' (%d already present) from %s",
             added, tenant_id, skipped, images_dir)
    return added, skipped


def _read_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        return {row["file"]: row for row in csv.DictReader(fh) if row.get("file")}


def _stored_path(path: Path) -> str:
    """Store paths relative to the project root so they work in every container."""
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def process(tenant_id: str, force: bool, wait: bool, timeout_s: float, job_types: list[str]) -> int:
    settings = get_settings()
    job_ids = []
    with get_sessionmaker()() as session:
        for job_type in job_types:
            job = enqueue(session, tenant_id, job_type, force=force, settings=settings)
            session.commit()
            job_ids.append(job.id)
            model = vision_model_name(settings) + " + " if job_type == "process_images" else ""
            print(f"Enqueued {job_type} job {job.id}: {job.total} item(s) "
                  f"(provider={settings.ai_provider}, models={model}{embed_model_name(settings)})")
    if not wait:
        return 0

    deadline = time.monotonic() + timeout_s
    last: dict = {}
    finished: dict = {}
    while time.monotonic() < deadline and len(finished) < len(job_ids):
        with get_sessionmaker()() as session:
            for job_id in job_ids:
                if job_id in finished:
                    continue
                job = jobs_repo.get(session, tenant_id, job_id)
                line = (f"  {job.type:<14} {job.status:<9} {job.processed}/{job.total} processed · "
                        f"{job.succeeded} ok · {job.flagged} flagged · {job.failed} failed")
                if line != last.get(job_id):
                    print(line, flush=True)
                    last[job_id] = line
                if job.finished_at is not None:
                    finished[job_id] = job.status
                    if job.error:
                        print(f"  error: {job.error}")
        if len(finished) < len(job_ids):
            time.sleep(2)
    if len(finished) < len(job_ids):
        print(f"Timed out after {timeout_s:.0f}s. Is the worker running? (docker compose up -d worker)")
        return 2
    return 0 if all(status in ("succeeded", "partial") for status in finished.values()) else 1


def snapshot_export(tenant_id: str) -> int:
    from app.services import snapshot
    from app.services.jobs import embed_model_name
    from app.services.taxonomy import current_taxonomy

    settings = get_settings()
    with get_sessionmaker()() as session:
        counts = snapshot.export(session, tenant_id, settings.resolve_path(SNAPSHOT_FILE),
                                 embed_model=embed_model_name(settings), taxonomy=current_taxonomy(settings))
    print(f"Recorded tenant '{tenant_id}' to {SNAPSHOT_FILE}: {counts['images']} images, {counts['posts']} posts, "
          f"{counts['labels']} taxonomy labels (vision output, post topics and every vector)")
    return 0


def snapshot_load(tenant_id: str) -> dict:
    from app.services import snapshot
    from app.services.taxonomy import current_taxonomy
    from app.services.thresholds import current_thresholds

    settings = get_settings()
    data = snapshot.read(settings.resolve_path(SNAPSHOT_FILE))
    with get_sessionmaker()() as session, session.begin():
        result = snapshot.load(session, tenant_id, data, taxonomy=current_taxonomy(settings),
                               thresholds=current_thresholds(settings), settings=settings)
    log.info("Replayed %s into tenant '%s': %d images, %d posts (no AI calls)",
             SNAPSHOT_FILE, tenant_id, result["images"], result["posts"])
    return {**result, "created_at": data["created_at"]}


def snapshot_load_into_existing(tenant_id: str) -> None:
    from app.services import snapshot
    from app.services.taxonomy import current_taxonomy
    from app.services.thresholds import current_thresholds

    settings = get_settings()
    data = snapshot.read(settings.resolve_path(SNAPSHOT_FILE))
    with get_sessionmaker()() as session, session.begin():
        result = snapshot.load(session, tenant_id, data, taxonomy=current_taxonomy(settings),
                               thresholds=current_thresholds(settings), settings=settings, fresh=False)
    log.info("Replayed recorded AI outputs into tenant '%s': %d images, %d posts (no AI calls, no cost)",
             tenant_id, result["images"], result["posts"])


def run_eval(tenant_id: str | None, do_sweep: bool, apply: bool) -> int:
    from app.services import evaluation
    from app.services.jobs import embed_model_name
    from app.services.thresholds import current_thresholds

    settings = get_settings()
    eval_set = evaluation.load_eval_set(settings.resolve_path(LABELS_FILE))
    true_class = evaluation.load_true_classes(settings.resolve_path(MANIFEST_FILE))
    if tenant_id is None:
        replay = snapshot_load(EVAL_TENANT)
        tenant_id, model = EVAL_TENANT, replay["embed_model"]
        source = f"recorded AI outputs {SNAPSHOT_FILE} ({replay['created_at']}), replayed into tenant '{tenant_id}'"
    else:
        model = embed_model_name(settings)
        source = f"live data in tenant '{tenant_id}'"
    thresholds = current_thresholds(settings)
    with get_sessionmaker()() as session:
        rankings = evaluation.collect_rankings(session, tenant_id, eval_set, model)
        metrics, outcomes = evaluation.evaluate(rankings, eval_set, thresholds, true_class)
        rows = evaluation.sweep(rankings, eval_set, thresholds, true_class) if do_sweep else None

    m = metrics
    print(f"Eval set {eval_set.version} (sha256 {eval_set.digest}): {m.positives} posts with one correct image, "
          f"{m.class_level} general posts, {m.negatives} off-topic posts, {m.hard_negatives} hard negatives")
    print(f"Source: {source} · embeddings {model}")
    t = thresholds.public()
    print(f"Guard {t['guard_version']} · similarity_min {t['similarity_min']} · confidence_min {t['confidence_min']} "
          f"· label_min {t['label_min']} · label_margin {t['label_margin']}\n")
    print(f"Top-1 precision: {m.headline()}")
    pwa = f"{m.precision_when_answering:.1%}" if m.precision_when_answering is not None else "n/a"
    print(f"  precision when answering {m.correct}/{m.answered} = {pwa} · coverage {m.answered}/{m.positives}")
    print(f"  general posts answered with the right class {m.class_level_correct}/{m.class_level} · off-topic "
          f"refused {m.correct_refusals}/{m.negatives} · hard negatives refused {m.hard_negatives_refused}/"
          f"{m.hard_negatives}")
    print(f"  cross-species false accepts: {m.cross_species_false_accepts} (similarity alone: "
          f"{m.baseline_cross_species})\n")
    print(f"  {'post':42} {'kind':13} {'expected':13} {'suggested':13} {'sim':>5} {'rank*':>5}  result")
    labels = {"positive": "one image", "class": "class-level", "negative": "off-topic", "hard_negative": "hard negative"}
    for o in outcomes:
        sim = f"{o.suggested_similarity:.3f}" if o.suggested_similarity is not None else "  -  "
        if o.kind in ("negative", "hard_negative"):
            result = "ok (refused)" if o.correct else ("FALSE ACCEPT (wrong animal)" if o.cross_species
                                                        else f"false accept (same class: {o.suggested_class})")
        else:
            result = "ok" if o.correct else ("WRONG ANIMAL" if o.cross_species else
                                             "miss (refused)" if not o.suggested else "miss")
        print(f"  {o.slug[:42]:42} {labels[o.kind]:13} {o.expected or '-':13} {o.suggested or 'no match':13} "
              f"{sim:>5} {o.expected_rank or '-':>5}  {result}")
    print("  * rank of the labeled image by pure similarity, before the guard")

    payload = evaluation.results_payload(metrics, outcomes, eval_set, thresholds, source=source, tenant_id=tenant_id,
                                         embed_model=model, sweep_rows=rows)
    if rows:
        best = evaluation.choose_threshold(rows)
        print("\n                  ------------------- full guard -------------------   -- similarity only --")
        print("  similarity_min  top-1   general  off-topic  hard neg.  cross-sp.   top-1   false acc  cross-sp.")
        for r in rows:
            mark = "  <- chosen" if r.similarity_min == best.similarity_min else ""
            fa = r.negatives + r.hard_negatives
            print(f"  {r.similarity_min:>14.2f}  {r.top1_precision:>6.1%}  {r.class_level_correct:>3}/{r.class_level:<3}  "
                  f"{r.correct_refusals:>4}/{r.negatives:<4} {r.hard_negatives_refused:>4}/{r.hard_negatives:<4} "
                  f"{r.cross_species_false_accepts:>6}    {r.baseline_top1_precision:>6.1%}  "
                  f"{r.baseline_false_accepts:>4}/{fa:<4}  {r.baseline_cross_species:>5}{mark}")
        print("\nRule: zero cross-species false accepts, then the highest top-1 precision, then every general post"
              " still answered, then the most refusals, then the middle of that plateau"
              f" -> similarity_min {best.similarity_min:.2f}")
        payload["chosen_similarity_min"] = best.similarity_min
        if apply:
            _apply_similarity(settings.resolve_path(settings.guard_file), best.similarity_min, eval_set)
            print(f"Saved similarity_min {best.similarity_min:.2f} to {settings.guard_file}. Run eval again to report it.")
    if tenant_id == EVAL_TENANT and not do_sweep:
        settings.resolve_path(RESULTS_FILE).write_text(json.dumps(payload, indent=1) + "\n")
        print(f"\nWrote {RESULTS_FILE}")
    return 0


def _apply_similarity(path: Path, value: float, eval_set) -> None:
    text = path.read_text()
    stamp = f"# similarity_min chosen by `cli eval --sweep` on {date.today()} (eval set {eval_set.version}, " \
            f"sha256 {eval_set.digest})"
    text = re.sub(r"(?m)^# similarity_min chosen by .*\n", "", text)
    text = re.sub(r"(?m)^similarity_min: .*$", f"{stamp}\nsimilarity_min: {value:.2f}", text, count=1)
    path.write_text(text)


def main() -> None:
    configure_logging(get_settings())
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="create demo tenants and register images (idempotent)")
    p_seed.add_argument("--images-dir", default=None, help="default: IMAGES_DIR setting (data/images)")
    p_seed.add_argument("--tenant", default="demo")
    p_seed.add_argument("--posts-file", default="data/posts.yaml")
    p_seed.add_argument("--snapshot", action="store_true",
                        help=f"also replay recorded AI outputs ({SNAPSHOT_FILE}) into the tenant: no key needed")

    p_proc = sub.add_parser("process", help="enqueue the batch jobs (images and posts)")
    p_proc.add_argument("--tenant", default="demo")
    p_proc.add_argument("--only", choices=["images", "posts"], default=None,
                        help="run one job type (default: images, then posts)")
    p_proc.add_argument("--force", action="store_true", help="re-run the AI step for items already done")
    p_proc.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p_proc.add_argument("--timeout", type=float, default=1800)

    p_snap = sub.add_parser("snapshot", help=f"record a tenant's AI outputs to {SNAPSHOT_FILE}")
    p_snap.add_argument("--tenant", default="demo")

    p_eval = sub.add_parser("eval", help="top-1 precision on the labeled eval set")
    p_eval.add_argument("--tenant", default=None,
                        help=f"evaluate live data in this tenant (default: replay {SNAPSHOT_FILE} into '{EVAL_TENANT}')")
    p_eval.add_argument("--sweep", action="store_true", help="sweep similarity_min 0.30-0.90 and pick a value")
    p_eval.add_argument("--apply", action="store_true", help="with --sweep: save the chosen value to config/guard.yaml")

    args = parser.parse_args()
    settings = get_settings()
    if args.command == "seed":
        seed(settings.resolve_path(args.images_dir or settings.images_dir), args.tenant,
             settings.resolve_path(args.posts_file))
        if args.snapshot:
            snapshot_load_into_existing(args.tenant)
    elif args.command == "process":
        types = {"images": ["process_images"], "posts": ["process_posts"]}.get(args.only,
                                                                               ["process_images", "process_posts"])
        sys.exit(process(args.tenant, args.force, args.wait, args.timeout, types))
    elif args.command == "snapshot":
        sys.exit(snapshot_export(args.tenant))
    elif args.command == "eval":
        sys.exit(run_eval(args.tenant, args.sweep, args.apply))


if __name__ == "__main__":
    main()
