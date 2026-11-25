"""Idempotent POSTs (shared requirement #5): replaying a request with the same Idempotency-Key returns
the stored response instead of doing the work again."""
import hashlib
import json
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.errors import Conflict
from app.repositories import idempotency as repo


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def run_once(session: Session, tenant_id: str, key: str | None, payload: dict,
             action: Callable[[], tuple[int, dict]]) -> tuple[int, dict, bool]:
    """Returns (status_code, body, replayed). The action and the stored key commit together."""
    if key is None:
        status, body = action()
        session.commit()
        return status, body, False

    digest = request_hash(payload)
    existing = repo.get(session, tenant_id, key)
    if existing is not None:
        return _replay(existing, digest)

    status, body = action()
    try:
        repo.save(session, tenant_id, key, request_hash=digest, status_code=status, response=body)
        session.commit()
    except IntegrityError:
        # A concurrent request with the same key won the race: undo our work and return its result.
        session.rollback()
        existing = repo.get(session, tenant_id, key)
        if existing is None:
            raise
        return _replay(existing, digest)
    return status, body, False


def _replay(existing, digest: str) -> tuple[int, dict, bool]:
    if existing.request_hash != digest:
        raise Conflict("Idempotency-Key was already used with a different request body")
    return existing.status_code, existing.response, True
