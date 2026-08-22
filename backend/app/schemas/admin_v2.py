from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

type AdminOptionValue = bool | int | str
type AdminOptionRequestValue = StrictBool | StrictInt | StrictStr


class AdminV2OptionField(BaseModel):
    key: str
    label: str
    description: str
    input_type: Literal["boolean", "integer", "select"]
    value: AdminOptionValue
    default: AdminOptionValue
    unit: str | None
    minimum: int | None
    maximum: int | None
    choices: list[str]
    editable: bool
    restart_required: bool
    version: int


class AdminV2OptionSection(BaseModel):
    id: str
    label: str
    fields: list[AdminV2OptionField]


class AdminV2SchedulerStatus(BaseModel):
    desired_generation: int
    applied_generation: int
    synchronized: bool
    rounds: int
    lease_active: bool


class AdminV2StorageStatus(BaseModel):
    managed_bytes: int
    logical_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    pressure: Literal["normal", "warning", "critical"]
    managed_quota_bytes: int
    user_quota_bytes: int


class AdminV2OptionAudit(BaseModel):
    key: str
    version: int
    old_value: object | None
    new_value: object
    actor: str | None
    source: str
    changed_at: datetime


class AdminV2Overview(BaseModel):
    sections: list[AdminV2OptionSection]
    scheduler: AdminV2SchedulerStatus
    storage: AdminV2StorageStatus
    audit: list[AdminV2OptionAudit]
    changed_keys: list[str] = Field(default_factory=list)
    restart_required: bool = False


class AdminV2OptionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: Annotated[dict[str, AdminOptionRequestValue], Field(min_length=1, max_length=64)]
