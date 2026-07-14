import os
import asyncio
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from data_fetcher import CoinGeckoFetcher
from scorer import SignalScorer


BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "rankings.db"


# --- Database helpers ---

def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            data_json TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS token_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id TEXT NOT NULL,
            token_name TEXT NOT NULL,
            token_symbol TEXT NOT NULL,
            signal_score REAL,
            token_rank INTEGER,
            snapshot_at TEXT NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_history_token_id ON token_history(token_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_history_snapshot ON token_history(snapshot_at)")
    conn.commit()
    conn.close()


def save_snapshot(coins: list[dict]):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO rankings (snapshot_at, data_json) VALUES (?, ?)",
        (now, json.dumps(coins, default=str))
    )
    for coin in coins:
        c.execute(
            "INSERT INTO token_history (token_id, token_name, token_symbol, signal_score, token_rank, snapshot_at) VALUES (?, ?, ?, ?, ?, ?)",
            (coin.get("id",""), coin.get("name",""), coin.get("symbol",""), coin.get("signal_score",0), coin.get("rank",0), now)
        )
    conn.commit()
    conn.close()


def load_latest_snapshot() -> list[dict] | None:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT data_json FROM rankings ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return json.loads(row[0]) if row else None


# --- FastAPI app ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_background_refresher())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="CryptoRadar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    global _last_refresh_time
    coins = load_latest_snapshot()
    if not coins:
        fetcher = CoinGeckoFetcher()
        scorer = SignalScorer()
        raw = fetcher.fetch_all()
        if raw:
            coins = scorer.score(raw)
            save_snapshot(coins)
            _last_refresh_time = datetime.now(timezone.utc)
        else:
            coins = []
    trending_up = sum(1 for c in coins if (c.get("price_change_percentage_24h") or 0) > 0)
    trending_down = len(coins) - trending_up

    context = {
        "request": request,
        "coins": coins or [],
        "updated_at": _last_refresh_time.strftime("%Y-%m-%d %H:%M UTC") if _last_refresh_time else "Never",
        "total_coins": len(coins),
        "trending_up": trending_up,
        "trending_down": trending_down,
    }
    return templates.TemplateResponse("index.html", context)


@app.post("/refresh")
async def refresh_data():
    global _last_refresh_time
    try:
        fetcher = CoinGeckoFetcher()
        scorer = SignalScorer()
        raw = fetcher.fetch_all()
        if raw:
            coins = scorer.score(raw)
            save_snapshot(coins)
            _last_refresh_time = datetime.now(timezone.utc)
            return {"status": "ok", "updated_at": _last_refresh_time.isoformat(), "count": len(coins)}
        return {"status": "error", "message": "No data from API"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/last-refresh")
async def api_last_refresh():
    return {"last_refresh": _last_refresh_time.isoformat() if _last_refresh_time else None}


@app.get("/api/rankings")
async def api_rankings():
    coins = load_latest_snapshot()
    return coins if coins else []


@app.get("/token/{token_id}", response_class=HTMLResponse)
async def token_detail(request: Request, token_id: str):
    latest = load_latest_snapshot()
    current = None
    if latest:
        for c in latest:
            if c.get("id") == token_id:
                current = c
                break
    history = get_token_history(token_id)
    context = {
        "request": request,
        "token": current,
        "history": history,
        "token_id": token_id,
    }
    return templates.TemplateResponse("token.html", context)


@app.get("/api/token/{token_id}/history")
async def api_token_history(token_id: str):
    return get_token_history(token_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
_last_refresh_time = None
_BACKGROUND_INTERVAL = 3600
_last_refresh_time = None
_BACKGROUND_INTERVAL = 3600


async def _background_refresher():
    global _last_refresh_time
    while True:
        try:
            fetcher = CoinGeckoFetcher()
            scorer = SignalScorer()
            raw = fetcher.fetch_all()
            if raw:
                coins = scorer.score(raw)
                save_snapshot(coins)
                _last_refresh_time = datetime.now(timezone.utc)
                print(f"[Background] Refreshed at {_last_refresh_time}")
            else:
                await asyncio.sleep(300)
                continue
        except Exception as e:
            print(f"[Background] Error: {e}")
            await asyncio.sleep(300)
            continue
        print(f"[Background] Next refresh in 1 hour")
        await asyncio.sleep(_BACKGROUND_INTERVAL)
def get_token_history(token_id: str, limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT token_name, token_symbol, signal_score, token_rank, snapshot_at FROM token_history WHERE token_id = ? ORDER BY snapshot_at DESC LIMIT ?",
        (token_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        result.append({
            "name": row[0],
            "symbol": row[1],
            "signal_score": row[2],
            "rank": row[3],
            "snapshot_at": row[4],
        })
    result.reverse()
    return result

