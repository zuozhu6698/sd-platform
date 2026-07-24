from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from sd_agent import worker_main
from sd_agent.config import Settings


class ImmediateEvent:
    def set(self) -> None:
        return None

    async def wait(self) -> None:
        return None


class FakeLoop:
    def __init__(self, *, unsupported: bool = False) -> None:
        self.unsupported = unsupported
        self.registered = 0

    def add_signal_handler(self, *_args: object) -> None:
        if self.unsupported:
            raise NotImplementedError
        self.registered += 1


class FakeResources:
    def __init__(self, *, outbox: object | None = None) -> None:
        self.closed = False
        self.outbox = outbox

    async def close(self) -> None:
        self.closed = True


class FakeLogger:
    async def ainfo(self, _event: str, **_values: object) -> None:
        return None


async def test_worker_starts_and_closes_resources(monkeypatch: Any) -> None:
    loop = FakeLoop()
    resources = FakeResources()
    monkeypatch.setattr(worker_main.asyncio, "Event", ImmediateEvent)
    monkeypatch.setattr(worker_main.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(worker_main, "logger", FakeLogger())
    monkeypatch.setattr(
        worker_main.RuntimeResources,
        "create",
        lambda _settings: resources,
    )

    await worker_main.run_worker(Settings(_env_file=None, ENV="test"))

    assert loop.registered == 2
    assert resources.closed is True


async def test_worker_tolerates_windows_signal_limit(monkeypatch: Any) -> None:
    resources = FakeResources()
    monkeypatch.setattr(worker_main.asyncio, "Event", ImmediateEvent)
    monkeypatch.setattr(worker_main, "logger", FakeLogger())
    monkeypatch.setattr(
        worker_main.asyncio,
        "get_running_loop",
        lambda: FakeLoop(unsupported=True),
    )
    monkeypatch.setattr(
        worker_main.RuntimeResources,
        "create",
        lambda _settings: resources,
    )

    await worker_main.run_worker(Settings(_env_file=None, ENV="test"))

    assert resources.closed is True


async def test_worker_rejects_enabled_outbox_without_database(monkeypatch: Any) -> None:
    resources = FakeResources()
    monkeypatch.setattr(worker_main.asyncio, "Event", ImmediateEvent)
    monkeypatch.setattr(worker_main, "logger", FakeLogger())
    monkeypatch.setattr(
        worker_main.asyncio,
        "get_running_loop",
        lambda: FakeLoop(),
    )
    monkeypatch.setattr(
        worker_main.RuntimeResources,
        "create",
        lambda _settings: resources,
    )

    try:
        await worker_main.run_worker(Settings(_env_file=None, ENV="test", OUTBOX_ENABLED=True))
    except RuntimeError as exc:
        assert str(exc) == "OUTBOX_ENABLED requires database and registered handlers"
    else:
        raise AssertionError("expected missing database configuration to fail")

    assert resources.closed is True


def test_main_delegates_to_asyncio_run(monkeypatch: Any) -> None:
    called = False

    def fake_run(coroutine: Coroutine[Any, Any, None]) -> None:
        nonlocal called
        called = True
        coroutine.close()

    monkeypatch.setattr(worker_main.asyncio, "run", fake_run)
    worker_main.main()
    assert called is True
