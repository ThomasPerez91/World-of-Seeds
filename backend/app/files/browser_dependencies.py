from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.files.browser import SandboxedFileBrowser
from app.files.dependencies import WorkspaceManagerDependency
from app.files.directory_sizes import DirectorySizeCalculator
from app.options.dependencies import OptionsStoreDependency


@lru_cache(maxsize=16)
def get_directory_size_calculator(
    max_scan_entries: int,
    cache_seconds: int,
) -> DirectorySizeCalculator:
    return DirectorySizeCalculator(cache_seconds=cache_seconds)


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
    max_scan_entries = _integer_option(options, "WOS_DIRECTORY_SIZE_MAX_ENTRIES")
    cache_seconds = _integer_option(options, "WOS_DIRECTORY_SIZE_CACHE_SECONDS")
    return SandboxedFileBrowser(
        workspace_manager,
        get_directory_size_calculator(max_scan_entries, cache_seconds),
        max_directory_entries=max_directory_entries,
        max_size_scan_entries=max_scan_entries,
    )


FileBrowserDependency = Annotated[SandboxedFileBrowser, Depends(get_file_browser)]
