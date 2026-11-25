"""Command-line entry points: python -m app.cli <command>

  seed      create demo tenants and register every image in data/images (idempotent: sha256 dedupe)
  process   enqueue a vision batch job; --wait polls until the worker finishes it
"""
import argparse
import csv
import hashlib
import logging
import sys
import time
from pathlib import Path

from app.config import PROJECT_ROOT, get_settings
from app.db.session import get_sessionmaker
from app.logging_config import configure_logging
from app.repositories import images as images_repo
from app.repositories import jobs as jobs_repo
from app.repositories import tenants
from app.services.jobs import enqueue_process_images, vision_model_name

log = logging.getLogger("app.cli")

DEMO_TENANTS = {
    "demo": "Demo tenant (all probes run here)",
    "other": "Second tenant, used to prove tenant isolation",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def seed(images_dir: Path | None = None, tenant_id: str = "demo") -> tuple[int, int]:
    with get_sessionmaker()() as session, session.begin():
        for tid, name in DEMO_TENANTS.items():
            tenants.upsert(session, tid, name)
    log.info("Seeded tenants: %s", ", ".join(DEMO_TENANTS))
    if images_dir is None:
        return 0, 0
    return register_images(images_dir, tenant_id)


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


def process(tenant_id: str, force: bool, wait: bool, timeout_s: float) -> int:
    settings = get_settings()
    with get_sessionmaker()() as session:
        job = enqueue_process_images(session, tenant_id, force=force, vision_model=vision_model_name(settings),
                                     settings=settings)
        session.commit()
        job_id, total = job.id, job.total
    print(f"Enqueued job {job_id}: {total} image(s) to process (provider={settings.ai_provider}, "
          f"model={vision_model_name(settings)})")
    if not wait:
        return 0

    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        with get_sessionmaker()() as session:
            job = jobs_repo.get(session, tenant_id, job_id)
        line = (f"  {job.status:<9} {job.processed}/{job.total} processed · {job.succeeded} tagged · "
                f"{job.flagged} flagged · {job.failed} failed")
        if line != last:
            print(line, flush=True)
            last = line
        if job.finished_at is not None:
            if job.error:
                print(f"  error: {job.error}")
            return 0 if job.status in ("succeeded", "partial") else 1
        time.sleep(2)
    print(f"Timed out after {timeout_s:.0f}s. Is the worker running? (docker compose up -d worker)")
    return 2


def main() -> None:
    configure_logging(get_settings())
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="create demo tenants and register images (idempotent)")
    p_seed.add_argument("--images-dir", default=None, help="default: IMAGES_DIR setting (data/images)")
    p_seed.add_argument("--tenant", default="demo")

    p_proc = sub.add_parser("process", help="enqueue the vision batch job")
    p_proc.add_argument("--tenant", default="demo")
    p_proc.add_argument("--force", action="store_true", help="re-process images already tagged")
    p_proc.add_argument("--wait", action="store_true", help="poll until the job finishes")
    p_proc.add_argument("--timeout", type=float, default=1800)

    args = parser.parse_args()
    settings = get_settings()
    if args.command == "seed":
        seed(settings.resolve_path(args.images_dir or settings.images_dir), args.tenant)
    elif args.command == "process":
        sys.exit(process(args.tenant, args.force, args.wait, args.timeout))


if __name__ == "__main__":
    main()
