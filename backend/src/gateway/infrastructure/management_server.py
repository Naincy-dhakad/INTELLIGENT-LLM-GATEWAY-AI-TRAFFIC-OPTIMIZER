"""Uvicorn construction for the isolated management application."""

from collections.abc import Callable

import uvicorn

from gateway.infrastructure.management_listener import ManagementListenerSpec


def create_management_server(
    spec: ManagementListenerSpec | None,
    *,
    server_factory: Callable[[uvicorn.Config], object] = uvicorn.Server,
) -> object | None:
    """Construct, but do not start, the management Uvicorn server."""
    if spec is None:
        return None
    config = uvicorn.Config(spec.app, host=spec.host, port=spec.port)
    return server_factory(config)
