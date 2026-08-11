import uuid
from typing import Annotated, Literal, Never

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth.dependencies import (
    AuthContext,
    require_current_credentials,
    require_current_credentials_csrf,
)
from app.files import (
    BrowserPathBlockedError,
    BrowserPathNotDirectoryError,
    BrowserPathNotFoundError,
    FileMutationError,
    InvalidRelativePathError,
    MutationCollisionError,
    MutationCompensationError,
    MutationInvalidTargetError,
    MutationProtectedPathError,
    MutationUnsupportedTypeError,
    WorkspaceError,
)
from app.schemas.trash import (
    RestoredTrashEntryResponse,
    TrashEntryResponse,
    TrashFileRequest,
    TrashListingResponse,
)
from app.trash import (
    TrashCompensationError,
    TrashEntryNotFoundError,
    TrashPersistenceError,
    TrashPurgeError,
    TrashRestoreTargetMissingError,
    TrashStorageError,
    TrashStorageMissingError,
    TrashStorageUnsafeError,
)
from app.trash.dependencies import TrashServiceDependency

router = APIRouter()


def _raise_trash_error(exc: Exception) -> Never:
    if isinstance(exc, InvalidRelativePathError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid relative path",
        ) from exc
    if isinstance(exc, (TrashEntryNotFoundError, TrashStorageMissingError)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trash entry not found",
        ) from exc
    if isinstance(exc, BrowserPathNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        ) from exc
    if isinstance(
        exc,
        (BrowserPathNotDirectoryError, MutationInvalidTargetError, MutationUnsupportedTypeError),
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid trash operation",
        ) from exc
    if isinstance(exc, (BrowserPathBlockedError, MutationProtectedPathError)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path is blocked",
        ) from exc
    if isinstance(exc, (MutationCollisionError, TrashRestoreTargetMissingError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The original location is unavailable or already occupied",
        ) from exc
    if isinstance(exc, TrashStorageUnsafeError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trash entry failed its integrity check",
        ) from exc
    if isinstance(exc, (MutationCompensationError, TrashCompensationError)):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Trash operation could not be rolled back safely",
        ) from exc
    if isinstance(exc, TrashPurgeError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permanent deletion did not complete",
        ) from exc
    if isinstance(
        exc,
        (FileMutationError, TrashPersistenceError, TrashStorageError, WorkspaceError),
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trash storage is unavailable",
        ) from exc
    raise exc


@router.get("", response_model=TrashListingResponse)
async def list_trash(
    service: TrashServiceDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> TrashListingResponse:
    listing = await service.list_entries(context.user.id)
    return TrashListingResponse(
        entries=[TrashEntryResponse.model_validate(entry) for entry in listing.entries],
        truncated=listing.truncated,
    )


@router.post("", response_model=TrashEntryResponse, status_code=status.HTTP_201_CREATED)
async def trash_file(
    payload: TrashFileRequest,
    service: TrashServiceDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> TrashEntryResponse:
    try:
        entry = await service.move_to_trash(context.user, payload.path)
    except Exception as exc:
        _raise_trash_error(exc)
    return TrashEntryResponse.model_validate(entry)


@router.post("/{entry_id}/restore", response_model=RestoredTrashEntryResponse)
async def restore_trash_entry(
    entry_id: uuid.UUID,
    service: TrashServiceDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> RestoredTrashEntryResponse:
    try:
        entry = await service.restore(context.user, entry_id)
    except Exception as exc:
        _raise_trash_error(exc)
    restored_kind: Literal["directory", "file"] = (
        "directory" if entry.kind.value == "directory" else "file"
    )
    return RestoredTrashEntryResponse(
        path=entry.original_path,
        name=entry.name,
        kind=restored_kind,
    )


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def purge_trash_entry(
    entry_id: uuid.UUID,
    service: TrashServiceDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> Response:
    try:
        await service.purge(context.user.id, entry_id)
    except Exception as exc:
        _raise_trash_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
