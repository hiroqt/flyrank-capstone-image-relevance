"""Failure alerts (shared requirement #3): DB row + ERROR log + optional webhook."""
import logging
import uuid

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.repositories import alerts as alerts_repo

log = logging.getLogger("app.alerts")


def raise_alert(session: Session, tenant_id: str, message: str, *, job_id: uuid.UUID | None = None,
                level: str = "error") -> None:
    alerts_repo.add(session, tenant_id, level=level, message=message, job_id=job_id)
    log.error("ALERT tenant=%s job=%s: %s", tenant_id, job_id, message)
    _post_webhook(tenant_id, job_id, level, message)


def _post_webhook(tenant_id: str, job_id: uuid.UUID | None, level: str, message: str) -> None:
    url = get_settings().alert_webhook_url
    if not url:
        return
    try:
        # "content" works for Discord; Slack-compatible webhooks read "text".
        text = f"[{level}] tenant={tenant_id} job={job_id}: {message}"
        httpx.post(url, json={"content": text, "text": text}, timeout=5)
    except httpx.HTTPError as exc:
        log.warning("Alert webhook failed: %s", type(exc).__name__)
