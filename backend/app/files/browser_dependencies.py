from typing import Annotated

from fastapi import Depends

from app.files.browser import SandboxedFileBrowser
from app.files.dependencies import WorkspaceManagerDependency
from app.options.dependencies import OptionsStoreDependency


def _integer_option(options: dict[str, bool | int | str], key: str) -> int:
    value = options[key]
    if type(value) is not int:
        raise RuntimeError(f"File browser option {key} has an invalid type")
    return value


def get_file_browser(
    workspace_manager: WorkspaceManagerDependency,
    options_store: OptionsStoreDependency,
) -> SandboxedFileBrowser:
    options = options_store.snapshot()
    max_directory_entries = _integer_option(options, "WOS_FILES_LIST_MAX_ENTRIES")
    return SandboxedFileBrowser(
        workspace_manager,
        max_directory_entries=max_directory_entries,
    )


FileBrowserDependency = Annotated[SandboxedFileBrowser, Depends(get_file_browser)]
