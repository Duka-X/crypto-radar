import os
import asyncio
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from data_fetcher import CoinGeckoFetcher
from scorer import SignalScorer


BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "rankings.db"
COMMUNITY_DATA = BASE_DIR / "data" / "community_data.json"
DESCRIPTION_CACHE_FILE = BASE_DIR / "data" / "description_cache.json"


# --- Database helpers ---

COMMUNITY_THRESHOLD = 100


def _load_desc_cache() -> dict:
    if not DESCRIPTION_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(DESCRIPTION_CACHE_FILE.read_text())
    except:
        return {}


def _save_desc_cache(cache: dict):
    DESCRIPTION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESCRIPTION_CACHE_FILE.write_text(json.dumps(cache, default=str))


def _get_description(token_id: str) -> str | None:
    """Fetch & cache token description from CoinGecko."""
    cache = _load_desc_cache()
    if token_id in cache:
        v = cache.get(token_id)
        return v if v else None
    try:
        import requests
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{token_id}",
            params={"localization": "false", "tickers": "false",
                    "market_data": "false", "community_data": "false",
                    "developer_data": "false", "sparkline": "false"},
            timeout=15,
            headers={"Accept": "application/json", "User-Agent": "CryptoRadar/1.0"}
        )
        if r.status_code == 200:
            data = r.json()
            desc = (data.get("description") or {}).get("en") or ""
            if desc:
                import re as _re
                desc = _re.sub(r"<[^>]+>", "", desc)
                desc = _re.sub(r"\s+", " ", desc).strip()
            cache[token_id] = desc
            _save_desc_cache(cache)
            return desc if desc else None
        else:
            cache[token_id] = ""
            _save_desc_cache(cache)
            return None
    except Exception as e:
        print(f"[Desc] {token_id}: {e}")
        return None


def save_community_snapshot(data: dict):
    now = datetime.now(timezone.utc).isoformat()
    record = {"ts": now}
    for cid, vals in data.items():
        record[cid] = vals
    if COMMUNITY_DATA.exists():
        snapshots = json.loads(COMMUNITY_DATA.read_text())
    else:
        snapshots = []
    snapshots.append(record)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    snapshots = [s for s in snapshots if s.get("ts", "") > cutoff]
    COMMUNITY_DATA.write_text(json.dumps(snapshots, default=str))

def get_latest_growth() -> dict:
    if not COMMUNITY_DATA.exists():
        return {}
    snapshots = json.loads(COMMUNITY_DATA.read_text())
    if len(snapshots) < 2:
        return {}
    prev, curr = snapshots[-2], snapshots[-1]
    growth = {}
    for token_id, curr_vals in curr.items():
        if token_id == "ts":
            continue
        prev_vals = prev.get(token_id, {})
        if not isinstance(prev_vals, dict):
            prev_vals = {}
        g = 0.0
        for key in ("twitter", "telegram", "reddit"):
            p = float(prev_vals.get(key, 0) or 0)
            c = float(curr_vals.get(key, 0) or 0)
            base = max(p, COMMUNITY_THRESHOLD)
            if base > 0:
                g += (c - p) / base * 100
        growth[token_id] = g
    return growth



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
    community_task = asyncio.create_task(_community_poller())
    yield
    task.cancel()
    community_task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="CryptoRadar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _patch_community(coins):
    if not coins:
        return
    try:
        scores = []
        for coin in coins:
            dev = float(coin.get("community_score", 0) or 0)
            mc = float(coin.get("market_cap", 0) or 1)
            proxy = m.log(1 + abs(mc)) * 0.05
            scores.append(round(dev + proxy, 4))
        order = sorted(range(len(scores)), key=lambda i: scores[i])
        for rank_pos, idx in enumerate(order):
            c_val = round(5 + (rank_pos / (len(coins) - 1)) * 90, 1)
            coins[idx]["score_community"] = c_val
    except:
        pass


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    global _last_refresh_time
    coins = load_latest_snapshot()
    if not coins:
        coins = []
    _patch_community(coins)
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
        raw = await asyncio.to_thread(fetcher.fetch_all)
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
    try:
        coins = load_latest_snapshot()
        if not coins:
            return []
        growth = get_latest_growth() or {}
        comm_raw = {}
        if COMMUNITY_DATA.exists():
            snapshots = json.loads(COMMUNITY_DATA.read_text())
            if snapshots:
                latest = snapshots[-1]
                for token_id, vals in latest.items():
                    if token_id == "ts" or not isinstance(vals, dict):
                        continue
                    tf = float(vals.get("twitter", 0) or 0)
                    tg = float(vals.get("telegram", 0) or 0)
                    rs = float(vals.get("reddit", 0) or 0)
                    try:
                        comm_raw[token_id] = m.log(1 + tf) * 0.05 + m.log(1 + tg) * 0.08 + m.log(1 + rs) * 0.1
                    except:
                        comm_raw[token_id] = 0.0
        for coin in coins:
            cid = coin.get("id", "")
            if not cid:
                continue
            if cid not in comm_raw:
                db_raw = coin.get("community_raw") or 0
                if isinstance(db_raw, (int, float)) and db_raw > 0:
                    comm_raw[cid] = float(db_raw)
        for coin in coins:
            cid = coin.get("id", "")
            if not cid:
                continue
            if cid in comm_raw and comm_raw[cid] > 0.01:
                continue
            try:
                mc = float(coin.get("market_cap", 0) or 1)
                vol = float(coin.get("total_volume", 0) or 1)
                proxy = m.log(1 + abs(mc)) * 0.03 + m.log(1 + abs(vol)) * 0.01
                comm_raw[cid] = max(comm_raw.get(cid, 0), proxy)
            except:
                comm_raw[cid] = max(comm_raw.get(cid, 0), 0.01)
        dev_scores = [float(coin.get("community_score", 0) or 0) for coin in coins]
        raw_scores = [float(comm_raw.get(coin.get("id", "") or "", 0)) for coin in coins]
        def to_range(vals, hi):
            if not vals:
                return []
            mn, mx = min(vals), max(vals)
            if mx > mn:
                return [(v - mn) / (mx - mn) * hi for v in vals]
            return [0.0] * len(vals)
        dev_norm = to_range(dev_scores, 50)
        raw_norm = to_range(raw_scores, 50)
        for i, coin in enumerate(coins):
            g = float(growth.get(coin.get("id", "") or "", 0))
            g_bonus = g / 20 if g > 5 else 0
            combined = dev_norm[i] + raw_norm[i] + g_bonus
            coin["community_growth"] = g
            coin["score_community"] = round(combined, 1)
        return coins
    except Exception as e:
        print(f"[API] /api/rankings error: {e}")
        return load_latest_snapshot() or []


@app.get("/debug")
async def debug_snapshot():
    coins = load_latest_snapshot()
    if not coins:
        return {"error": "no data"}
    c = coins[0]
    return {
        "name": c.get("name"),
        "sparkline_prices_len": len(c.get("sparkline_prices", []) or []),
        "sparkline_full_len": len(c.get("sparkline_full", []) or []),
        "sparkline_sample": (c.get("sparkline_prices", []) or [])[:3]
    }


@app.get("/token/{token_id}", response_class=HTMLResponse)
async def token_detail(request: Request, token_id: str):
    latest = load_latest_snapshot()
    current = None
    related = []
    if latest:
        for c in latest:
            if c.get("id") == token_id:
                current = c
            else:
                related.append(c)
        related = sorted(related, key=lambda x: x.get("signal_score", 0) or 0, reverse=True)[:5]
    history = get_token_history(token_id)
    description = _get_description(token_id) if current else None
    context = {
        "request": request,
        "token": current,
        "history": history,
        "token_id": token_id,
        "related_coins": related,
        "token_description": description,
    }
    return templates.TemplateResponse("token.html", context)


@app.get("/api/token/{token_id}/history")
async def api_token_history(token_id: str):
    return get_token_history(token_id)


@app.get("/api/token/{token_id}/chart")
async def token_chart(token_id: str, days: int = 7):
    try:
        r = __import__("requests").get(
            f"https://api.coingecko.com/api/v3/coins/{token_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            timeout=30,
            headers={"Accept": "application/json", "User-Agent": "CryptoRadar/1.0"}
        )
        if r.status_code == 200:
            return r.json()
        return {"error": f"API returned {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/robots.txt", response_class=Response, include_in_schema=False)
async def robots_txt():
    return Response(content="""User-agent: *
Disallow: /api/
Disallow: /refresh

Sitemap: https://cryptoradar.dev/sitemap.xml
""", media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response, include_in_schema=False)
async def sitemap_xml():
    coins = load_latest_snapshot()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        "    <loc>https://cryptoradar.dev/</loc>",
        "    <changefreq>hourly</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
    ]
    for c in (coins or []):
        cid = c.get("id", "")
        if cid:
            lines.extend([
                "  <url>",
                f"    <loc>https://cryptoradar.dev/token/{cid}</loc>",
                "    <changefreq>hourly</changefreq>",
                "    <priority>0.8</priority>",
                "  </url>",
            ])
    lines.append("</urlset>")
    return Response(content="\n".join(lines), media_type="application/xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
_last_refresh_time = None
_BACKGROUND_INTERVAL = 3600


async def _community_poller():
    from data_fetcher import CoinGeckoFetcher
    while True:
        try:
            await asyncio.sleep(60)
            fetcher = CoinGeckoFetcher()
            coins = load_latest_snapshot()
            if coins:
                ids = [c["id"] for c in coins if c.get("id")]
                community = await asyncio.to_thread(fetcher.fetch_community_data, ids)
                if community:
                    save_community_snapshot(community)
        except Exception as e:
            print(f"[Community] Poll error: {e}")


async def _background_refresher():
    global _last_refresh_time
    while True:
        try:
            fetcher = CoinGeckoFetcher()
            scorer = SignalScorer()
            raw = await asyncio.to_thread(fetcher.fetch_all)
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

