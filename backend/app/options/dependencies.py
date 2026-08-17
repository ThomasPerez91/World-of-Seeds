from typing import Annotated, cast

from fastapi import Depends, Request

from app.options.store import OptionsStore


def get_options_store(request: Request) -> OptionsStore:
    return cast(OptionsStore, request.app.state.options_store)


OptionsStoreDependency = Annotated[OptionsStore, Depends(get_options_store)]
