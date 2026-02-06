"""Configuration system — TOML file + env var overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScanSettings:
    interval: int = 60
    min_profit: float = 0.005
    max_markets: int = 100
    fee_rate: float = 0.02
    use_orderbooks: bool = True
    min_liquidity: float = 0.0
    min_volume: float = 0.0


@dataclass
class ApiSettings:
    concurrency: int = 10
    timeout: float = 15.0
    max_retries: int = 3
    client_refresh_interval: int = 3600  # seconds


@dataclass
class LogSettings:
    level: str = "INFO"
    file: str = "polytrage.log"
    max_bytes: int = 10_485_760  # 10 MB
    backup_count: int = 5


@dataclass
class NotifySettings:
    discord_webhook: str = ""
    cooldown: int = 300  # seconds per-market
    on_startup: bool = True
    on_error: bool = True
    on_arb: bool = True


@dataclass
class HealthSettings:
    enabled: bool = True
    heartbeat_file: str = "heartbeat.json"
    stale_threshold: int = 300  # seconds


@dataclass
class StorageSettings:
    enabled: bool = True
    trades_file: str = "trades.jsonl"
    max_memory: int = 1000


@dataclass
class Config:
    scan: ScanSettings = field(default_factory=ScanSettings)
    api: ApiSettings = field(default_factory=ApiSettings)
    log: LogSettings = field(default_factory=LogSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)
    health: HealthSettings = field(default_factory=HealthSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    headless: bool = False
    paper: bool = False


def _apply_section(target: object, data: dict) -> None:
    """Apply dict values to a dataclass, ignoring unknown keys."""
    for key, value in data.items():
        if hasattr(target, key):
            expected_type = type(getattr(target, key))
            try:
                setattr(target, key, expected_type(value))
            except (ValueError, TypeError):
                pass


def load_config(
    path: str | Path | None = None,
    *,
    env_prefix: str = "POLYTRAGE_",
) -> Config:
    """Load config from TOML file, then overlay env var overrides.

    Priority: env vars > TOML file > defaults.
    """
    cfg = Config()

    # 1. Load TOML if available
    if path is not None:
        toml_path = Path(path)
        if toml_path.exists():
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
            section_map = {
                "scan": cfg.scan,
                "api": cfg.api,
                "log": cfg.log,
                "notify": cfg.notify,
                "health": cfg.health,
                "storage": cfg.storage,
            }
            for section_name, section_obj in section_map.items():
                if section_name in data:
                    _apply_section(section_obj, data[section_name])
            # Top-level booleans
            if "headless" in data:
                cfg.headless = bool(data["headless"])
            if "paper" in data:
                cfg.paper = bool(data["paper"])

    # 2. Env var overrides (flat: POLYTRAGE_DISCORD_WEBHOOK, POLYTRAGE_LOG_LEVEL, etc.)
    env_map = {
        "DISCORD_WEBHOOK": (cfg.notify, "discord_webhook"),
        "LOG_LEVEL": (cfg.log, "level"),
        "LOG_FILE": (cfg.log, "file"),
        "SCAN_INTERVAL": (cfg.scan, "interval"),
        "MIN_PROFIT": (cfg.scan, "min_profit"),
        "MAX_MARKETS": (cfg.scan, "max_markets"),
        "FEE_RATE": (cfg.scan, "fee_rate"),
        "API_CONCURRENCY": (cfg.api, "concurrency"),
        "API_TIMEOUT": (cfg.api, "timeout"),
        "HEARTBEAT_FILE": (cfg.health, "heartbeat_file"),
        "TRADES_FILE": (cfg.storage, "trades_file"),
    }
    for suffix, (section, attr) in env_map.items():
        env_val = os.environ.get(f"{env_prefix}{suffix}")
        if env_val is not None:
            expected_type = type(getattr(section, attr))
            try:
                setattr(section, attr, expected_type(env_val))
            except (ValueError, TypeError):
                pass

    return cfg
