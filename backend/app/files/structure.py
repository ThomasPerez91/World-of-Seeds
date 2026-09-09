import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any


class WorkspaceStructureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceStructure:
    schema_version: int
    directories: tuple[str, ...]
    visible_directories: tuple[str, ...]
    protected_directories: tuple[str, ...]
    retired_directories: tuple[str, ...]
    trash_directory: str


def _safe_component(value: object, *, field: str) -> str:
    if not isinstance(value, str) or value in {"", ".", ".."}:
        raise WorkspaceStructureError(f"{field} must be a non-empty path component")
    if "/" in value or "\\" in value or "\0" in value:
        raise WorkspaceStructureError(f"{field} must be a single path component")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise WorkspaceStructureError(f"{field} contains a control character")
    if len(value.encode("utf-8")) > 255:
        raise WorkspaceStructureError(f"{field} is too long")
    return value


def _object(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkspaceStructureError(f"{field} must be an object")
    return value


def _objects(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkspaceStructureError(f"{field} must be a list")
    return [_object(item, field=f"{field}[]") for item in value]


def load_workspace_structure() -> WorkspaceStructure:
    resource = files("app.files").joinpath("workspace_structure.json")
    try:
        raw = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceStructureError("Workspace structure cannot be loaded") from exc

    root = _object(raw, field="root")
    if root.get("schema_version") != 1:
        raise WorkspaceStructureError("Unsupported workspace structure version")

    workspace = _object(root.get("workspace"), field="workspace")
    directory_records = _objects(workspace.get("directories"), field="workspace.directories")
    retired_records = _objects(
        workspace.get("retired_directories"),
        field="workspace.retired_directories",
    )
    directories = tuple(
        _safe_component(record.get("name"), field="workspace.directories[].name")
        for record in directory_records
    )
    visible = tuple(
        name
        for name, record in zip(directories, directory_records, strict=True)
        if record.get("visible") is True
    )
    protected = tuple(
        name
        for name, record in zip(directories, directory_records, strict=True)
        if record.get("protected") is True
    )
    retired = tuple(
        _safe_component(record.get("name"), field="workspace.retired_directories[].name")
        for record in retired_records
        if record.get("remove_if_empty") is True
    )

    trash = _object(root.get("trash"), field="trash")
    trash_directory = _safe_component(trash.get("root"), field="trash.root")
    if trash.get("scope") != "user_id" or trash.get("visible_in_browser") is not False:
        raise WorkspaceStructureError("Trash must remain isolated outside user workspaces")

    all_names = (*directories, *retired, trash_directory)
    if len(set(all_names)) != len(all_names):
        raise WorkspaceStructureError("Workspace structure contains duplicate names")
    if not directories or set(visible) != set(directories):
        raise WorkspaceStructureError("All current workspace directories must be visible")
    if not set(protected).issubset(directories):
        raise WorkspaceStructureError("Protected directories must belong to the workspace")

    return WorkspaceStructure(
        schema_version=1,
        directories=directories,
        visible_directories=visible,
        protected_directories=protected,
        retired_directories=retired,
        trash_directory=trash_directory,
    )


WORKSPACE_STRUCTURE = load_workspace_structure()
