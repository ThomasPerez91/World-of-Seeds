from typing import Annotated, cast

from fastapi import Depends
from starlette.requests import HTTPConnection

from app.coordination.redis import RedisCoordinator


def get_redis_coordinator(connection: HTTPConnection) -> RedisCoordinator:
    return cast(RedisCoordinator, connection.app.state.redis_coordinator)


RedisCoordinatorDependency = Annotated[RedisCoordinator, Depends(get_redis_coordinator)]
