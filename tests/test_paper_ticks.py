import asyncio
import threading
from collections import deque
from types import SimpleNamespace
import pytest
import backend.paper_ticks as ticks_module


def test_silent_stream_closes_and_reconnects(monkeypatch):
    calls, closed = [], []
    async def stream(*args, **kwargs):
        calls.append(1)
        try:
            await asyncio.Event().wait()
            yield {}
        finally:
            closed.append(1)
    original_wait = asyncio.wait_for
    original_sleep = asyncio.sleep
    async def fast_wait(awaitable, timeout):
        return await original_wait(awaitable, timeout=0.01)
    async def fast_sleep(delay):
        if len(calls) >= 2:
            raise asyncio.CancelledError
        await original_sleep(0)
    monkeypatch.setattr(ticks_module, 'client', SimpleNamespace(stream=stream))
    monkeypatch.setattr(ticks_module.asyncio, 'wait_for', fast_wait)
    monkeypatch.setattr(ticks_module.asyncio, 'sleep', fast_sleep)
    receiver = ticks_module.PaperTicks.__new__(ticks_module.PaperTicks)
    receiver.code = 'NFO_123'
    receiver.lock = threading.Lock()
    receiver.connected = True
    receiver.stopped = threading.Event()
    receiver.attempts = 0
    receiver.last_error = None
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(receiver.run())
    assert len(calls) == len(closed) == 2
    assert receiver.connected is False


def test_stop_marks_disconnected_and_cancels_task():
    from unittest.mock import Mock
    receiver = ticks_module.PaperTicks.__new__(ticks_module.PaperTicks)
    receiver.stopped = threading.Event()
    receiver.lock = threading.Lock()
    receiver.connected = True
    receiver.loop = Mock()
    receiver.loop.is_closed.return_value = False
    receiver.task = Mock()
    receiver.stop()
    assert receiver.stopped.is_set()
    assert not receiver.connected
    receiver.loop.call_soon_threadsafe.assert_called_once_with(receiver.task.cancel)
