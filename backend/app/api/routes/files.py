from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.auth.dependencies import AuthContext, require_current_credentials
from app.files import (
    BrowserPathBlockedError,
    BrowserPathNotDirectoryError,
    BrowserPathNotFoundError,
    DownloadPathNotFileError,
    InvalidRelativePathError,
    RangeNotSatisfiableError,
    WorkspaceError,
)
from app.files.browser_dependencies import FileBrowserDependency
from app.files.download_dependencies import FileDownloaderDependency
from app.files.downloads import (
    ByteRange,
    DownloadStreamingResponse,
    if_range_matches,
    parse_range_header,
)
from app.schemas.files import (
    BreadcrumbResponse,
    DirectoryListingResponse,
    FileEntryResponse,
    StorageUsageResponse,
)

router = APIRouter()


@router.get("/download", response_model=None, operation_id="download_file")
@router.head("/download", response_model=None, operation_id="head_file_download")
async def download_file(
    request: Request,
    downloader: FileDownloaderDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    path: Annotated[str, Query(max_length=4096)],
) -> Response:
    try:
        download = downloader.open(context.user.username, path)
    except InvalidRelativePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid relative path",
        ) from exc
    except BrowserPathNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        ) from exc
    except (BrowserPathNotDirectoryError, DownloadPathNotFileError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a regular file",
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

    response_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": download.content_disposition,
        "Content-Type": download.media_type,
        "ETag": download.etag,
        "Last-Modified": download.last_modified,
    }
    selected_range: ByteRange | None = None
    range_header = request.headers.get("range")
    if range_header is not None and if_range_matches(
        request.headers.get("if-range"),
        download,
    ):
        try:
            selected_range = parse_range_header(range_header, download.size)
        except RangeNotSatisfiableError:
            download.close()
            response_headers.update(
                {
                    "Content-Length": "0",
                    "Content-Range": f"bytes */{download.size}",
                }
            )
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers=response_headers,
            )

    if selected_range is None:
        response_status = status.HTTP_200_OK
        start = 0
        length = download.size
    else:
        response_status = status.HTTP_206_PARTIAL_CONTENT
        start = selected_range.start
        length = selected_range.length
        response_headers["Content-Range"] = (
            f"bytes {selected_range.start}-{selected_range.end}/{download.size}"
        )
    response_headers["Content-Length"] = str(length)

    if request.method == "HEAD":
        download.close()
        return Response(status_code=response_status, headers=response_headers)

    return DownloadStreamingResponse(
        download,
        start=start,
        length=length,
        status_code=response_status,
        headers=response_headers,
    )


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
