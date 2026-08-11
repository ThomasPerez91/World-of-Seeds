from typing import Annotated

from fastapi import Depends

from app.files.dependencies import WorkspaceManagerDependency
from app.files.mutations import SandboxedFileMutator


def get_file_mutator(workspace_manager: WorkspaceManagerDependency) -> SandboxedFileMutator:
    return SandboxedFileMutator(workspace_manager)


FileMutatorDependency = Annotated[SandboxedFileMutator, Depends(get_file_mutator)]
