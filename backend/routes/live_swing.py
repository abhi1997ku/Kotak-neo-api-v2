import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from backend.live_swing_service import service
from screener.live_swing import LIVE_DB
import sqlite3

router = APIRouter(prefix='/index-swing/live', tags=['index-swing-live'])


@router.post('/start')
async def start():
    try:
        return await service.start()
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


@router.post('/stop')
async def stop():
    return await service.stop()


@router.get('/status')
async def status():
    return service.snapshot()


@router.get('/export')
def export():
    rows = []
    if LIVE_DB.exists():
        with sqlite3.connect(LIVE_DB) as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM trades ORDER BY rowid')]
    return Response(''.join(json.dumps(r, allow_nan=False)+'\n' for r in rows), media_type='application/x-ndjson',
                    headers={'Content-Disposition': 'attachment; filename="kotak-forward-paper.jsonl"'})
