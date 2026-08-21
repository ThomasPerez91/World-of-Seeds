from typing import Annotated, Never

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.auth.dependencies import (
    AuthContext,
    require_current_credentials,
    require_current_credentials_csrf,
)
from app.files import (
    BrowserPathBlockedError,
    BrowserPathNotDirectoryError,
    BrowserPathNotFoundError,
    DownloadPathNotFileError,
    FileMutationError,
    InvalidRelativePathError,
    MutationCollisionError,
    MutationCompensationError,
    MutationInvalidTargetError,
    MutationProtectedPathError,
    MutationUnsupportedTypeError,
    RangeNotSatisfiableError,
    WorkspaceError,
)
from app.files.archives import (
    ArchiveBusyError,
    ArchiveError,
    ArchiveStreamingResponse,
    ArchiveTooLargeError,
)
from app.files.browser_dependencies import FileBrowserDependency
from app.files.download_dependencies import FileDownloaderDependency, FolderArchiverDependency
from app.files.downloads import (
    ByteRange,
    DownloadStreamingResponse,
    if_range_matches,
    parse_range_header,
)
from app.files.mutation_dependencies import FileMutatorDependency
from app.options.dependencies import OptionsStoreDependency
from app.schemas.files import (
    BreadcrumbResponse,
    CreateDirectoryRequest,
    DirectoryListingResponse,
    FileEntryResponse,
    FileMutationResponse,
    MoveFileRequest,
    RenameFileRequest,
    StorageUsageResponse,
)

router = APIRouter()


def _raise_mutation_error(exc: Exception) -> Never:
    if isinstance(exc, InvalidRelativePathError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid relative path",
        ) from exc
    if isinstance(exc, BrowserPathNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File or destination not found",
        ) from exc
    if isinstance(
        exc,
        (BrowserPathNotDirectoryError, MutationInvalidTargetError, MutationUnsupportedTypeError),
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file operation",
        ) from exc
    if isinstance(exc, (BrowserPathBlockedError, MutationProtectedPathError)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path is blocked",
        ) from exc
    if isinstance(exc, MutationCollisionError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Destination already exists",
        ) from exc
    if isinstance(exc, MutationCompensationError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File operation could not be verified",
        ) from exc
    if isinstance(exc, (FileMutationError, WorkspaceError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File operation is unavailable",
        ) from exc
    raise exc


@router.post("/directory", response_model=FileMutationResponse, status_code=status.HTTP_201_CREATED)
def create_directory(
    payload: CreateDirectoryRequest,
    mutator: FileMutatorDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> FileMutationResponse:
    try:
        result = mutator.create_directory(context.user.username, payload.parent, payload.name)
    except (
        InvalidRelativePathError,
        BrowserPathNotFoundError,
        BrowserPathNotDirectoryError,
        BrowserPathBlockedError,
        MutationCollisionError,
        MutationCompensationError,
        FileMutationError,
        WorkspaceError,
    ) as exc:
        _raise_mutation_error(exc)
    return FileMutationResponse.model_validate(result)


@router.patch("/rename", response_model=FileMutationResponse)
def rename_file(
    payload: RenameFileRequest,
    mutator: FileMutatorDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> FileMutationResponse:
    try:
        result = mutator.rename(context.user.username, payload.path, payload.basename)
    except (
        InvalidRelativePathError,
        BrowserPathNotFoundError,
        BrowserPathNotDirectoryError,
        BrowserPathBlockedError,
        MutationInvalidTargetError,
        MutationUnsupportedTypeError,
        MutationProtectedPathError,
        MutationCollisionError,
        MutationCompensationError,
        FileMutationError,
        WorkspaceError,
    ) as exc:
        _raise_mutation_error(exc)
    return FileMutationResponse.model_validate(result)


@router.post("/move", response_model=FileMutationResponse)
def move_file(
    payload: MoveFileRequest,
    mutator: FileMutatorDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> FileMutationResponse:
    try:
        result = mutator.move(
            context.user.username,
            payload.path,
            payload.destination_directory,
        )
    except (
        InvalidRelativePathError,
        BrowserPathNotFoundError,
        BrowserPathNotDirectoryError,
        BrowserPathBlockedError,
        MutationInvalidTargetError,
        MutationUnsupportedTypeError,
        MutationProtectedPathError,
        MutationCollisionError,
        MutationCompensationError,
        FileMutationError,
        WorkspaceError,
    ) as exc:
        _raise_mutation_error(exc)
    return FileMutationResponse.model_validate(result)


@router.get("/download", response_model=None, operation_id="download_file")
@router.head("/download", response_model=None, operation_id="head_file_download")
async def download_file(
    request: Request,
    downloader: FileDownloaderDependency,
    options_store: OptionsStoreDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    path: Annotated[str, Query(max_length=4096)],
) -> Response:
    options = options_store.snapshot()
    chunk_size = options["WOS_HTTP_STREAM_CHUNK_BYTES"]
    if type(chunk_size) is not int:
        raise RuntimeError("Download chunk size option has an invalid type")
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
        chunk_size=chunk_size,
    )


@router.get("/download-folder", response_model=None, operation_id="download_folder")
async def download_folder(
    archiver: FolderArchiverDependency,
    options_store: OptionsStoreDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    path: Annotated[str, Query(max_length=4096)],
) -> Response:
    options = options_store.snapshot()
    max_source_bytes = options["WOS_FOLDER_ARCHIVE_MAX_BYTES"]
    chunk_size = options["WOS_HTTP_STREAM_CHUNK_BYTES"]
    if type(max_source_bytes) is not int or type(chunk_size) is not int:
        raise RuntimeError("Archive options have invalid types")
    try:
        archive = archiver.open(
            context.user.username,
            path,
            max_source_bytes=max_source_bytes,
        )
    except InvalidRelativePathError as exc:
        raise HTTPException(status_code=400, detail="Invalid relative path") from exc
    except BrowserPathNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Directory not found") from exc
    except BrowserPathNotDirectoryError as exc:
        raise HTTPException(status_code=400, detail="Path is not a directory") from exc
    except BrowserPathBlockedError as exc:
        raise HTTPException(status_code=403, detail="Path is blocked") from exc
    except ArchiveTooLargeError as exc:
        raise HTTPException(status_code=413, detail="Folder is too large to archive") from exc
    except ArchiveBusyError as exc:
        raise HTTPException(
            status_code=429,
            detail="Another folder archive is already running",
            headers={"Retry-After": "30"},
        ) from exc
    except (ArchiveError, WorkspaceError) as exc:
        raise HTTPException(status_code=503, detail="Folder archive is unavailable") from exc
    return ArchiveStreamingResponse(
        archive,
        archiver=archiver,
        max_source_bytes=max_source_bytes,
        chunk_size=chunk_size,
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
