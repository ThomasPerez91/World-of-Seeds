"""Visible trace of administrative actions.

The message itself carries the fields: the default log format does not print ``extra``. Only
opaque identifiers and counts belong here, never names, passwords, keys or passkeys.
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger("app.audit")


def log_admin_action(action: str, actor_id: uuid.UUID, **fields: str | int | bool) -> None:
    detail = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    logger.info(
        "admin_action action=%s actor=%s%s",
        action,
        actor_id,
        f" {detail}" if detail else "",
    )
