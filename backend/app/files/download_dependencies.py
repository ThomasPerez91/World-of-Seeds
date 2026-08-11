from typing import Annotated

from fastapi import Depends

from app.files.dependencies import WorkspaceManagerDependency
from app.files.downloads import SandboxedFileDownloader


def get_file_downloader(
    workspace_manager: WorkspaceManagerDependency,
) -> SandboxedFileDownloader:
    return SandboxedFileDownloader(workspace_manager)


FileDownloaderDependency = Annotated[SandboxedFileDownloader, Depends(get_file_downloader)]
