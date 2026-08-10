from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.files.workspaces import WorkspaceManager


def get_workspace_manager(
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkspaceManager:
    return WorkspaceManager(settings.data_root)


WorkspaceManagerDependency = Annotated[WorkspaceManager, Depends(get_workspace_manager)]
