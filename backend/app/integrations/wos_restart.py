from pathlib import Path

from app.integrations.newgreedy_restart import (
    NewGreedyRestartError as WosRestartError,
)
from app.integrations.newgreedy_restart import (
    NewGreedyRestartPendingError as WosRestartPendingError,
)
from app.integrations.newgreedy_restart import (
    NewGreedyRestartStatus as WosRestartStatus,
)
from app.integrations.newgreedy_restart import (
    NewGreedyRestartStore,
)
from app.integrations.newgreedy_restart import (
    NewGreedyRestartUnavailableError as WosRestartUnavailableError,
)
from app.integrations.newgreedy_restart import (
    NewGreedyRestartUnsafeError as WosRestartUnsafeError,
)

WOS_REQUEST_DIRECTORY = "wos"
WOS_STATUS_DIRECTORY = "wos-status"


class WosRestartStore(NewGreedyRestartStore):
    """Fixed host-mediated restart channel for the WOS app service only."""

    def __init__(self, data_root: Path, *, status_owner_uid: int = 0) -> None:
        super().__init__(
            data_root,
            status_owner_uid=status_owner_uid,
            request_directory=WOS_REQUEST_DIRECTORY,
            status_directory=WOS_STATUS_DIRECTORY,
        )


__all__ = [
    "WosRestartError",
    "WosRestartPendingError",
    "WosRestartStatus",
    "WosRestartStore",
    "WosRestartUnavailableError",
    "WosRestartUnsafeError",
]
