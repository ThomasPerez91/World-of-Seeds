from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.files.browser import SandboxedFileBrowser
from app.files.dependencies import WorkspaceManagerDependency
from app.files.directory_sizes import DirectorySizeCalculator


@lru_cache(maxsize=1)
def get_directory_size_calculator() -> DirectorySizeCalculator:
    return DirectorySizeCalculator()


def get_file_browser(
    workspace_manager: WorkspaceManagerDependency,
) -> SandboxedFileBrowser:
    return SandboxedFileBrowser(workspace_manager, get_directory_size_calculator())


FileBrowserDependency = Annotated[SandboxedFileBrowser, Depends(get_file_browser)]
