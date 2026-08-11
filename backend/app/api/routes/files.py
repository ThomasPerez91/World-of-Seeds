from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.dependencies import AuthContext, require_current_credentials
from app.files import (
    BrowserPathBlockedError,
    BrowserPathNotDirectoryError,
    BrowserPathNotFoundError,
    InvalidRelativePathError,
    WorkspaceError,
)
from app.files.browser_dependencies import FileBrowserDependency
from app.schemas.files import (
    BreadcrumbResponse,
    DirectoryListingResponse,
    FileEntryResponse,
    StorageUsageResponse,
)

router = APIRouter()


@router.get("", response_model=DirectoryListingResponse)
def list_directory(
    browser: FileBrowserDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    path: Annotated[str, Query(max_length=4096)] = "",
) -> DirectoryListingResponse:
    try:
        snapshot = browser.list_directory(context.user.username, path)
    except InvalidRelativePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid relative path",
        ) from exc
    except BrowserPathNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Directory not found",
        ) from exc
    except BrowserPathNotDirectoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        ) from exc
    except BrowserPathBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path is blocked",
        ) from exc
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User storage is unavailable",
        ) from exc

    components = snapshot.path.split("/") if snapshot.path else []
    breadcrumbs = [BreadcrumbResponse(label="Mes fichiers", path="")]
    breadcrumbs.extend(
        BreadcrumbResponse(label=component, path="/".join(components[: index + 1]))
        for index, component in enumerate(components)
    )
    return DirectoryListingResponse(
        path=snapshot.path,
        breadcrumbs=breadcrumbs,
        entries=[FileEntryResponse.model_validate(entry) for entry in snapshot.entries],
        storage=StorageUsageResponse.model_validate(snapshot.storage),
        truncated=snapshot.truncated,
    )
