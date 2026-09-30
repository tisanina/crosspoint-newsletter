"""Unit and integration tests for PollingScheduler and background task execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crosspoint_newsletter.scheduler import PollingScheduler, run_pipeline_cycle
from crosspoint_newsletter.storage.database import Database
from crosspoint_newsletter.storage.file_store import FileStore
from crosspoint_newsletter.storage.models import Newsletter


@pytest.fixture
def test_setup(tmp_path: Path):
    conf_dir = tmp_path / "conf"
    data_dir = tmp_path / "data"
    conf_dir.mkdir()
    data_dir.mkdir()

    db = Database(conf_dir / "newsletter.db")
    fs = FileStore(base_dir=data_dir)

    now = datetime.now(UTC)
    nl = Newsletter(
        id="nl-sched-1",
        name="Scheduled Tech",
        slug="scheduled-tech",
        sender_email="sched@tech.com",
        created_at=now,
        updated_at=now,
    )
    db.upsert_newsletter(nl)
    return db, fs


@pytest.mark.anyio
async def test_scheduler_lifecycle(test_setup):
    db, fs = test_setup
    scheduler = PollingScheduler(db=db, file_store=fs, interval_minutes=10, enabled=True)

    status = scheduler.get_status()
    assert status["enabled"] is True
    assert status["is_polling"] is False
    assert status["interval_minutes"] == 10
    assert status["total_polls_run"] == 0

    scheduler.start()
    assert scheduler._running is True
    assert scheduler._task is not None

    await scheduler.stop()
    assert scheduler._running is False
    assert scheduler._task is None


@pytest.mark.anyio
async def test_scheduler_trigger_now(test_setup, monkeypatch):
    db, fs = test_setup
    scheduler = PollingScheduler(db=db, file_store=fs, interval_minutes=5, enabled=True)

    # Mock run_pipeline_cycle to avoid needing real IMAP network
    mock_called = False

    def mock_cycle(*args, **kwargs):
        nonlocal mock_called
        mock_called = True
        return {
            "success": True,
            "unseen_count": 2,
            "epub_generated": 2,
            "errors": 0,
            "retention_pruned": 0,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    monkeypatch.setattr("crosspoint_newsletter.scheduler.run_pipeline_cycle", mock_cycle)

    res = await scheduler.trigger_now()
    assert res["status"] == "success"
    assert res["result"]["epub_generated"] == 2
    assert mock_called is True
    assert scheduler.total_polls_run == 1
    assert scheduler.last_poll_at is not None
    assert scheduler.next_poll_at is not None


def test_run_pipeline_cycle_unconfigured_imap(test_setup, monkeypatch):
    db, fs = test_setup
    monkeypatch.setattr("crosspoint_newsletter.config.IMAP_HOST", "")
    monkeypatch.setattr("crosspoint_newsletter.config.IMAP_USER", "")

    result = run_pipeline_cycle(db=db, file_store=fs)
    assert result["success"] is False
    assert "Credenziali IMAP non configurate" in result["error"]
