from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

type OptionResponseValue = bool | int | str
type OptionRequestValue = StrictBool | StrictInt | StrictStr


class OptionFieldResponse(BaseModel):
    key: str
    label: str
    description: str
    input_type: Literal["boolean", "integer", "select"]
    value: OptionResponseValue
    default: OptionResponseValue
    unit: str | None
    minimum: int | None
    maximum: int | None
    choices: list[str]
    editable: bool
    restart_required: bool


class OptionSectionResponse(BaseModel):
    id: str
    label: str
    fields: list[OptionFieldResponse]


class OptionsResponse(BaseModel):
    sections: list[OptionSectionResponse]
    changed_keys: list[str] = Field(default_factory=list)
    restart_required: bool = False


class OptionsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: Annotated[dict[str, OptionRequestValue], Field(min_length=1, max_length=64)]
