"""Ensure paper UI cannot expose broker auth/order routes."""
import asyncio
from unittest.mock import AsyncMock
from backend.indstocks_app import app


def test_paper_app_has_only_indstocks_routes():
    paths = [r.path for r in app.routes if r.path.startswith('/api/')]
    assert paths
    assert all(p.startswith('/api/paper-data/indstocks/') for p in paths)
    assert '/api/orders' not in paths
    assert '/api/auth/status' not in paths


def test_closed_session_does_not_open_provider_stream(monkeypatch):
    import pandas as pd
    monkeypatch.setattr(pd.Timestamp, 'now', staticmethod(lambda **kwargs: pd.Timestamp('2026-10-17 12:00', tz='Asia/Kolkata')))
    from backend.routes.indstocks import stream
    ws = AsyncMock()
    asyncio.run(stream(ws))
    ws.send_json.assert_awaited_once_with({'state': 'Outside regular session'})
    ws.close.assert_awaited_once_with(code=1000)
