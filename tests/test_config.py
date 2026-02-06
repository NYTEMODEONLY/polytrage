"""Tests for the configuration system."""

import os
import textwrap

import pytest

from polytrage.config import Config, ScanSettings, load_config


class TestDefaults:
    """Config defaults should match expected values."""

    def test_default_config(self):
        cfg = Config()
        assert cfg.scan.interval == 60
        assert cfg.scan.min_profit == 0.005
        assert cfg.scan.max_markets == 100
        assert cfg.scan.fee_rate == 0.02
        assert cfg.scan.use_orderbooks is True
        assert cfg.api.concurrency == 10
        assert cfg.api.timeout == 15.0
        assert cfg.log.level == "INFO"
        assert cfg.log.max_bytes == 10_485_760
        assert cfg.log.backup_count == 5
        assert cfg.notify.discord_webhook == ""
        assert cfg.notify.cooldown == 300
        assert cfg.health.enabled is True
        assert cfg.storage.max_memory == 1000
        assert cfg.headless is False
        assert cfg.paper is False


class TestLoadFromTOML:
    """Load config from TOML files."""

    def test_load_basic_toml(self, tmp_path):
        toml_file = tmp_path / "test.toml"
        toml_file.write_text(textwrap.dedent("""\
            [scan]
            interval = 30
            min_profit = 0.01
            max_markets = 200

            [log]
            level = "DEBUG"

            [notify]
            discord_webhook = "https://discord.com/api/webhooks/test"
        """))

        cfg = load_config(str(toml_file))
        assert cfg.scan.interval == 30
        assert cfg.scan.min_profit == 0.01
        assert cfg.scan.max_markets == 200
        assert cfg.log.level == "DEBUG"
        assert cfg.notify.discord_webhook == "https://discord.com/api/webhooks/test"

    def test_load_nonexistent_toml(self, tmp_path):
        """Loading a nonexistent file returns defaults."""
        cfg = load_config(str(tmp_path / "missing.toml"))
        assert cfg.scan.interval == 60

    def test_load_none_path(self):
        """None path returns defaults."""
        cfg = load_config(None)
        assert cfg.scan.interval == 60

    def test_partial_toml(self, tmp_path):
        """Partial TOML only overrides specified values."""
        toml_file = tmp_path / "partial.toml"
        toml_file.write_text(textwrap.dedent("""\
            [scan]
            interval = 15
        """))

        cfg = load_config(str(toml_file))
        assert cfg.scan.interval == 15
        assert cfg.scan.min_profit == 0.005  # default preserved
        assert cfg.scan.max_markets == 100  # default preserved

    def test_top_level_booleans(self, tmp_path):
        toml_file = tmp_path / "bool.toml"
        toml_file.write_text(textwrap.dedent("""\
            headless = true
            paper = true
        """))

        cfg = load_config(str(toml_file))
        assert cfg.headless is True
        assert cfg.paper is True

    def test_unknown_keys_ignored(self, tmp_path):
        """Unknown keys in TOML should not raise."""
        toml_file = tmp_path / "extra.toml"
        toml_file.write_text(textwrap.dedent("""\
            [scan]
            interval = 45
            unknown_key = "ignored"
        """))

        cfg = load_config(str(toml_file))
        assert cfg.scan.interval == 45


class TestEnvOverrides:
    """Environment variable overrides."""

    def test_discord_webhook_env(self, monkeypatch):
        monkeypatch.setenv("POLYTRAGE_DISCORD_WEBHOOK", "https://hook.test")
        cfg = load_config(None)
        assert cfg.notify.discord_webhook == "https://hook.test"

    def test_log_level_env(self, monkeypatch):
        monkeypatch.setenv("POLYTRAGE_LOG_LEVEL", "WARNING")
        cfg = load_config(None)
        assert cfg.log.level == "WARNING"

    def test_scan_interval_env(self, monkeypatch):
        monkeypatch.setenv("POLYTRAGE_SCAN_INTERVAL", "120")
        cfg = load_config(None)
        assert cfg.scan.interval == 120

    def test_env_overrides_toml(self, tmp_path, monkeypatch):
        """Env vars should override TOML values."""
        toml_file = tmp_path / "test.toml"
        toml_file.write_text(textwrap.dedent("""\
            [scan]
            interval = 30
        """))
        monkeypatch.setenv("POLYTRAGE_SCAN_INTERVAL", "90")

        cfg = load_config(str(toml_file))
        assert cfg.scan.interval == 90  # env wins

    def test_invalid_env_ignored(self, monkeypatch):
        """Invalid env var types should be silently ignored."""
        monkeypatch.setenv("POLYTRAGE_SCAN_INTERVAL", "not_a_number")
        cfg = load_config(None)
        assert cfg.scan.interval == 60  # default preserved
