from typing import Annotated, cast

from fastapi import Depends, Request

from app.integrations.monitor import ExternalServicesMonitor
from app.integrations.newgreedy_config import NewGreedyConfigStore
from app.integrations.newgreedy_restart import NewGreedyRestartStore


def get_external_services_monitor(request: Request) -> ExternalServicesMonitor:
    return cast(ExternalServicesMonitor, request.app.state.external_services_monitor)


ExternalServicesMonitorDependency = Annotated[
    ExternalServicesMonitor,
    Depends(get_external_services_monitor),
]


def get_newgreedy_config_store(request: Request) -> NewGreedyConfigStore:
    return cast(NewGreedyConfigStore, request.app.state.newgreedy_config_store)


NewGreedyConfigStoreDependency = Annotated[
    NewGreedyConfigStore,
    Depends(get_newgreedy_config_store),
]


def get_newgreedy_restart_store(request: Request) -> NewGreedyRestartStore:
    return cast(NewGreedyRestartStore, request.app.state.newgreedy_restart_store)


NewGreedyRestartStoreDependency = Annotated[
    NewGreedyRestartStore,
    Depends(get_newgreedy_restart_store),
]
