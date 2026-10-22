import asyncio

import pytest
from fastapi import HTTPException


def test_retired_agent_cannot_start(monkeypatch,tmp_path):
    from backend import paper_agent
    from backend.paper_journal import PaperJournal
    monkeypatch.setattr(paper_agent,'PaperJournal',lambda:PaperJournal(tmp_path/'paper.db'))
    agent=paper_agent.PaperAgent()
    result=agent.start(object())
    assert result['strategy_retired'] is True
    assert result['running'] is False
    assert agent.task is None


def test_retired_route_rejects_without_login():
    from backend.routes.paper_trading import start
    with pytest.raises(HTTPException) as error:
        asyncio.run(start())
    assert error.value.status_code==410
