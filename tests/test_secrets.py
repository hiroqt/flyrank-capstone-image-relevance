"""Shared requirement #6: secrets come from env only and are never logged."""
import logging

from app.config import Settings
from app.logging_config import REDACTED, SecretRedactionFilter

KEY = "AIzaSy-test-not-a-real-key-123456"


def test_settings_repr_hides_secret():
    settings = Settings(gemini_api_key=KEY)
    assert KEY not in repr(settings)
    assert KEY not in str(settings.model_dump())


def test_secret_is_redacted_from_log_messages(caplog):
    logger = logging.getLogger("test.secrets")
    logger.addFilter(SecretRedactionFilter([KEY]))
    with caplog.at_level(logging.INFO, logger="test.secrets"):
        logger.info("calling provider with key=%s", KEY)
        logger.info(f"inline key {KEY}")
    assert KEY not in caplog.text
    assert caplog.text.count(REDACTED) == 2
