from typing import Annotated, cast

from fastapi import Depends, Request

from app.coordination.redis import RedisCoordinator


def get_redis_coordinator(request: Request) -> RedisCoordinator:
    return cast(RedisCoordinator, request.app.state.redis_coordinator)


RedisCoordinatorDependency = Annotated[RedisCoordinator, Depends(get_redis_coordinator)]
