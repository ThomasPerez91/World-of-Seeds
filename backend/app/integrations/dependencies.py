from typing import Annotated, cast

from fastapi import Depends, Request

from app.integrations.monitor import ExternalServicesMonitor


def get_external_services_monitor(request: Request) -> ExternalServicesMonitor:
    return cast(ExternalServicesMonitor, request.app.state.external_services_monitor)


ExternalServicesMonitorDependency = Annotated[
    ExternalServicesMonitor,
    Depends(get_external_services_monitor),
]
