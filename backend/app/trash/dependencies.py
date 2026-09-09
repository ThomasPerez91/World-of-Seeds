from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.files.dependencies import WorkspaceManagerDependency
from app.trash.filesystem import TrashFilesystem
from app.trash.service import TrashService


def get_trash_filesystem(
    settings: Annotated[Settings, Depends(get_settings)],
    workspace_manager: WorkspaceManagerDependency,
) -> TrashFilesystem:
    return TrashFilesystem(settings.data_root, workspace_manager)


TrashFilesystemDependency = Annotated[TrashFilesystem, Depends(get_trash_filesystem)]


def get_trash_service(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    filesystem: TrashFilesystemDependency,
) -> TrashService:
    return TrashService(db, filesystem)


TrashServiceDependency = Annotated[TrashService, Depends(get_trash_service)]
