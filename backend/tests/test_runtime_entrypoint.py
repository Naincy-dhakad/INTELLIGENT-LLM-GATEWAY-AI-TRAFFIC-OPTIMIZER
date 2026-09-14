import asyncio

from gateway.runtime import run


class FakeRuntime:
    def __init__(self, *, fail_start=False):
        self.fail_start = fail_start
        self.calls = []

    async def start(self):
        self.calls.append("start")
        if self.fail_start:
            raise RuntimeError("startup failure")

    async def supervise(self):
        self.calls.append("supervise")

    async def shutdown(self):
        self.calls.append("shutdown")


def test_run_starts_supervises_and_shuts_down_runtime():
    runtime = FakeRuntime()

    asyncio.run(run(runtime))

    assert runtime.calls == ["start", "supervise", "shutdown"]


def test_run_shuts_down_after_startup_failure():
    runtime = FakeRuntime(fail_start=True)

    try:
        asyncio.run(run(runtime))
    except RuntimeError as exc:
        assert str(exc) == "startup failure"
    else:
        raise AssertionError("startup failure was not propagated")

    assert runtime.calls == ["start", "shutdown"]
