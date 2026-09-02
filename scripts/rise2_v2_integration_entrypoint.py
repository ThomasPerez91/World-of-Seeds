#!/usr/bin/env python3
"""Load the existing Compose registry secret only inside its authorized process."""

import os
import sys
from pathlib import Path

if len(sys.argv) != 2 or sys.argv[1] not in {"app.worker", "app.scheduler_service"}:
    sys.exit("Invalid Rise2 integration process.")
try:
    registry = Path("/run/secrets/integration_registry").read_text(encoding="utf-8")
    if not registry or len(registry) > 1024 * 1024:
        raise ValueError
except (OSError, UnicodeError, ValueError):
    sys.exit("Rise2 integration registry secret unavailable.")
os.environ["WOS_INTEGRATION_ACCOUNTS_JSON"] = registry
os.execv(sys.executable, [sys.executable, "-m", sys.argv[1]])
