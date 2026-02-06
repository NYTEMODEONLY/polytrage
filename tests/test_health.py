"""Tests for health monitoring."""

import json
import time

import pytest

from polytrage.config import HealthSettings
from polytrage.health import check_health, write_heartbeat


class TestWriteHeartbeat:
    def test_writes_json_file(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(heartbeat_file=str(hb_file))

        write_heartbeat(settings, markets_scanned=50, opportunities=2, errors=1)

        data = json.loads(hb_file.read_text())
        assert data["markets_scanned"] == 50
        assert data["opportunities"] == 2
        assert data["errors"] == 1
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "iso" in data

    def test_disabled_skips_write(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(enabled=False, heartbeat_file=str(hb_file))

        write_heartbeat(settings, markets_scanned=10)

        assert not hb_file.exists()

    def test_overwrites_existing(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(heartbeat_file=str(hb_file))

        write_heartbeat(settings, markets_scanned=10)
        write_heartbeat(settings, markets_scanned=20)

        data = json.loads(hb_file.read_text())
        assert data["markets_scanned"] == 20

    def test_creates_parent_dirs(self, tmp_path):
        hb_file = tmp_path / "subdir" / "heartbeat.json"
        settings = HealthSettings(heartbeat_file=str(hb_file))

        write_heartbeat(settings, markets_scanned=5)

        assert hb_file.exists()


class TestCheckHealth:
    def test_healthy_fresh_heartbeat(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(
            heartbeat_file=str(hb_file),
            stale_threshold=300,
        )

        write_heartbeat(settings, markets_scanned=10)
        assert check_health(settings) is True

    def test_unhealthy_missing_file(self, tmp_path):
        settings = HealthSettings(
            heartbeat_file=str(tmp_path / "missing.json"),
        )
        assert check_health(settings) is False

    def test_unhealthy_stale_heartbeat(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(
            heartbeat_file=str(hb_file),
            stale_threshold=10,
        )

        # Write a stale heartbeat
        data = {
            "timestamp": time.time() - 60,  # 60 seconds ago
            "markets_scanned": 10,
            "status": "ok",
        }
        hb_file.write_text(json.dumps(data))

        assert check_health(settings) is False

    def test_unhealthy_malformed_json(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(heartbeat_file=str(hb_file))

        hb_file.write_text("not json")
        assert check_health(settings) is False

    def test_unhealthy_missing_timestamp(self, tmp_path):
        hb_file = tmp_path / "heartbeat.json"
        settings = HealthSettings(heartbeat_file=str(hb_file))

        hb_file.write_text(json.dumps({"status": "ok"}))
        assert check_health(settings) is False
