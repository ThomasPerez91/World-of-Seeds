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


def get_file_browser(
    workspace_manager: WorkspaceManagerDependency,
    options_store: OptionsStoreDependency,
) -> SandboxedFileBrowser:
    options = options_store.snapshot()
    max_directory_entries = options["WOS_FILES_LIST_MAX_ENTRIES"]
    max_scan_entries = options["WOS_DIRECTORY_SIZE_MAX_ENTRIES"]
    cache_seconds = options["WOS_DIRECTORY_SIZE_CACHE_SECONDS"]
    if not all(
        type(value) is int for value in (max_directory_entries, max_scan_entries, cache_seconds)
    ):
        raise RuntimeError("File browser options have invalid types")
    return SandboxedFileBrowser(
        workspace_manager,
        get_directory_size_calculator(max_scan_entries, cache_seconds),
        max_directory_entries=max_directory_entries,
        max_size_scan_entries=max_scan_entries,
    )


FileBrowserDependency = Annotated[SandboxedFileBrowser, Depends(get_file_browser)]
