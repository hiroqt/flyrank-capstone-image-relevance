"""Test setup: a real Postgres test database, migrated with Alembic (not create_all), truncated per test.

DATABASE_URL is pointed at the test DB *before* any app module is imported, so every
cached engine/settings object in the app uses it.
"""
import os
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5433/app_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Tests never call a real AI provider; the HTTP layer reads settings, so pin it to the fake one.
os.environ["AI_PROVIDER"] = "fake"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.cli import seed  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import get_engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logger"] = False
    return cfg


def _ensure_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    _ensure_database(TEST_DATABASE_URL)
    command.upgrade(alembic_config(), "head")
    yield


@pytest.fixture(autouse=True)
def clean_database(migrated_database):
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    seed()
    yield


@pytest.fixture
def alembic_cfg() -> Config:
    return alembic_config()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


# ---- shared helpers for pipeline / worker / API tests -----------------------------------------
import hashlib  # noqa: E402
import json  # noqa: E402

from PIL import Image as PILImage  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402
from app.jobs.worker import Worker  # noqa: E402
from app.providers.factory import build_ledger  # noqa: E402
from app.providers.fake import FakeEmbedder, FakeVision  # noqa: E402
from app.providers.tracked import TrackedEmbedder, TrackedVision  # noqa: E402
from app.repositories import images as images_repo  # noqa: E402
from app.services.jobs import enqueue_process_images  # noqa: E402
from app.services.taxonomy import Taxonomy, resolver_version  # noqa: E402
from app.services.thresholds import Thresholds  # noqa: E402

# Unit tests use their own tiny taxonomy, so they don't depend on the wording of config/taxonomy.yaml.
TEST_TAXONOMY = Taxonomy(
    version="test",
    labels={"fox": "fox", "wolf": "wolf", "dog": "dog", "bear": "bear", "deer": "deer"},
    out_of_scope={"cat": "cat", "not_an_animal": "bread food plant landscape object"},
)

# Thresholds for the bag-of-words FakeEmbedder (its scores run much lower than a real model's).
TEST_THRESHOLDS = Thresholds(guard_version="test", similarity_min=0.2, confidence_min=0.70,
                             label_min=0.15, label_margin=0.01)
TEST_RESOLVER_VERSION = resolver_version(TEST_TAXONOMY, TEST_THRESHOLDS.label_min, TEST_THRESHOLDS.label_margin)


def vision_json(**overrides) -> str:
    data = {
        "subject": "red fox",
        "category": "animal",
        "attributes": ["orange fur", "wild", "forest"],
        "caption": "A red fox standing in a forest",
        "confidence": 0.94,
    }
    data.update(overrides)
    return json.dumps(data)


@pytest.fixture
def test_settings() -> Settings:
    # No backoff, no rate limit, fake provider: deterministic and fast.
    return Settings(ai_provider="fake", job_backoff_base_s=0, job_backoff_max_s=0, vision_rpm=0, embed_rpm=0,
                    job_max_attempts=3, vision_max_repairs=2, ai_budget_usd=1.0)


@pytest.fixture
def add_image(tmp_path):
    """Create a real image file and register it for a tenant. Returns the image id."""
    counter = {"n": 0}

    def _add(tenant_id: str = "demo", color=(200, 90, 30)):
        counter["n"] += 1
        path = tmp_path / f"img_{counter['n']}.png"
        PILImage.new("RGB", (64, 48), (color[0], color[1], (color[2] + counter["n"]) % 256)).save(path)
        with get_sessionmaker()() as session, session.begin():
            images_repo.register(session, tenant_id, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 file_path=str(path))
        with get_sessionmaker()() as session:
            rows, _ = images_repo.list_(session, tenant_id, limit=200)
            return next(r.id for r in rows if r.file_path == str(path))

    return _add


@pytest.fixture
def enqueue(test_settings):
    def _enqueue(tenant_id: str = "demo", force: bool = False):
        with get_sessionmaker()() as session:
            job = enqueue_process_images(session, tenant_id, force=force, vision_model=FakeVision.model,
                                         settings=test_settings, resolver=TEST_RESOLVER_VERSION)
            session.commit()
            return job

    return _enqueue


@pytest.fixture
def make_worker(test_settings):
    def _make(script=(), *, settings: Settings | None = None, ledger=None, vision_factory=None,
              embedder=None, thresholds: Thresholds | None = None):
        settings = settings or test_settings
        session_factory = get_sessionmaker()
        ledger = ledger or build_ledger(settings, session_factory)
        fake = FakeVision(script)
        tracked = TrackedVision(fake, ledger)
        tracked_embedder = TrackedEmbedder(embedder or FakeEmbedder(), ledger)
        worker = Worker(settings, session_factory, vision_factory or (lambda: tracked), lambda: tracked_embedder,
                        taxonomy=TEST_TAXONOMY, thresholds=thresholds or TEST_THRESHOLDS, sleep=lambda _: None)
        return worker, fake

    return _make


def drain(worker: Worker, limit: int = 50) -> int:
    """Run the worker until the queue is empty. Returns how many items were claimed."""
    claimed = 0
    while claimed < limit and worker.run_once():
        claimed += 1
    return claimed
