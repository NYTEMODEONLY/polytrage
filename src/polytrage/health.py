"""Health monitoring — heartbeat file + health check subcommand."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from polytrage.config import HealthSettings


def write_heartbeat(
    settings: HealthSettings,
    *,
    markets_scanned: int = 0,
    opportunities: int = 0,
    errors: int = 0,
) -> None:
    """Write a JSON heartbeat file atomically after each scan."""
    if not settings.enabled:
        return

    data = {
        "timestamp": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "markets_scanned": markets_scanned,
        "opportunities": opportunities,
        "errors": errors,
        "status": "ok",
    }

    heartbeat_path = Path(settings.heartbeat_file)

    # Atomic write: write to temp file in same dir, then rename
    parent = heartbeat_path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with open(fd, "w") as f:
            json.dump(data, f)
        Path(tmp_path).replace(heartbeat_path)
    except Exception:
        # Clean up temp file on failure
        Path(tmp_path).unlink(missing_ok=True)
        raise


def check_health(settings: HealthSettings) -> bool:
    """Check if the heartbeat file is fresh. Returns True if healthy."""
    heartbeat_path = Path(settings.heartbeat_file)
    if not heartbeat_path.exists():
        return False

    try:
        with open(heartbeat_path) as f:
            data = json.load(f)
        age = time.time() - data["timestamp"]
        return age < settings.stale_threshold
    except (json.JSONDecodeError, KeyError, OSError):
        return False


def health_command(settings: HealthSettings) -> None:
    """CLI subcommand: exits 0 if healthy, 1 if stale/missing."""
    healthy = check_health(settings)
    if healthy:
        print("OK")
        sys.exit(0)
    else:
        print("UNHEALTHY")
        sys.exit(1)
