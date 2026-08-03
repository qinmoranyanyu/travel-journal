import logging

from app.logging_config import configure_logging, shutdown_logging


def test_error_log_includes_stack_redacts_secrets_and_rotates(tmp_path):
    secret = "private-api-key"
    log_file = configure_logging(
        tmp_path / "logs",
        level="INFO",
        secrets=(secret,),
        max_bytes=700,
        backup_count=2,
    )
    logger = logging.getLogger("app.test_logging")
    try:
        for index in range(30):
            logger.error("rotation_probe index=%d payload=%s", index, "x" * 80)
        try:
            raise ValueError(f"request failed with {secret}")
        except ValueError:
            logger.exception("pipeline_failed job_id=test key=%s", secret)
    finally:
        shutdown_logging()

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(log_file.parent.glob("travel-journal.log*"))
    )
    assert "pipeline_failed job_id=test" in combined
    assert "Traceback" in combined
    assert "ValueError" in combined
    assert "[REDACTED]" in combined
    assert secret not in combined
    assert (log_file.parent / "travel-journal.log.1").exists()
