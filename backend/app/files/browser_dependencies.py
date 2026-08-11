from typing import Annotated

from fastapi import Depends

from app.files.browser import SandboxedFileBrowser
from app.files.dependencies import WorkspaceManagerDependency


def get_file_browser(workspace_manager: WorkspaceManagerDependency) -> SandboxedFileBrowser:
    return SandboxedFileBrowser(workspace_manager)


FileBrowserDependency = Annotated[SandboxedFileBrowser, Depends(get_file_browser)]
