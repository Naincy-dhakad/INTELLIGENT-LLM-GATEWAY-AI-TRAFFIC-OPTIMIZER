"""Explicit production composition root for the gateway runtime."""

import asyncio

from gateway.config.settings import get_settings
from gateway.infrastructure.server_runtime import GatewayRuntime, create_gateway_runtime


async def run(runtime: GatewayRuntime) -> None:
    """Start, supervise, and always shut down one composed gateway runtime."""
    try:
        await runtime.start()
        await runtime.supervise()
    finally:
        await runtime.shutdown()


def main() -> None:
    """Run the public listener and optional management listener in one process."""
    settings = get_settings()
    runtime = create_gateway_runtime(settings)
    try:
        asyncio.run(run(runtime))
    except KeyboardInterrupt:
        # asyncio.run cancels the task and executes run()'s shutdown finally block.
        pass


if __name__ == "__main__":
    main()
