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

import math as m
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
        print(f"[Desc] {token_id}: HTTP {r.status_code}")
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
    # Snapshot ~1 hour ago vs latest
    curr = snapshots[-1]
    curr_ts = datetime.fromisoformat(curr["ts"])
    target_ts = (curr_ts - timedelta(hours=1)).isoformat()
    prev = snapshots[0]
    for snap in reversed(snapshots):
        if snap["ts"] <= target_ts:
            prev = snap
            break
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
            if c > 0 or p > 0:
                g += m.log(1 + c) - m.log(1 + p)
        growth[token_id] = g
    nonzero = sum(1 for v in growth.values() if v != 0)
    print(f"[Growth] {len(growth)} coins, {nonzero} with change")
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
    global _last_refresh_time
    init_db()
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute("SELECT snapshot_at FROM rankings ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            _last_refresh_time = datetime.fromisoformat(row[0])
        conn.close()
    except:
        pass
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


TWITTER_MIN_BASE = 1000


def _score_twitter_growth(coins):
    if not coins:
        return
    snapshots_file = BASE_DIR / "data" / "community_data.json"
    if not snapshots_file.exists():
        for c in coins:
            c["score_community"] = 50.0
        return
    snapshots = json.loads(snapshots_file.read_text())
    if len(snapshots) < 2:
        for c in coins:
            c["score_community"] = 50.0
        return
    prev, curr = snapshots[0], snapshots[-1]
    growth = {}
    for cid, cur_vals in curr.items():
        if cid == "ts":
            continue
        pv = prev.get(cid, {})
        if not isinstance(pv, dict):
            pv = {}
        p = float(pv.get("twitter", 0) or 0)
        c2 = float(cur_vals.get("twitter", 0) or 0)
        if p >= TWITTER_MIN_BASE:
            growth[cid] = (c2 - p) / p * 100 * 60 * 60
        else:
            growth[cid] = (c2 - p) * 60
    if not growth:
        return
    vals = list(growth.values())
    mn, mx = min(vals), max(vals)
    for coin in coins:
        cid = coin.get("id", "")
        if cid in growth and mx > mn:
            coin["score_community"] = round(
                (growth[cid] - mn) / (mx - mn) * 100, 1
            )
        else:
            coin["score_community"] = 50.0


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    global _last_refresh_time
    coins = load_latest_snapshot()
    if not coins:
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
    asyncio.create_task(_run_refresh_now())
    return {"status": "ok", "message": "refresh started"}

async def _run_refresh_now():
    global _last_refresh_time
    try:
        fetcher = CoinGeckoFetcher()
        scorer = SignalScorer()
        raw = await asyncio.to_thread(fetcher.fetch_all)
        if raw:
            coins = scorer.score(raw)
            save_snapshot(coins)
            _last_refresh_time = datetime.now(timezone.utc)
    except Exception as e:
        print(f"[Refresh] Error: {e}")


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
        # Community score from growth data only (no absolute size proxy)
        g_vals = [float(growth.get(c.get("id", ""), 0)) for c in coins]
        mn_g, mx_g = min(g_vals), max(g_vals)
        for i, coin in enumerate(coins):
            g = g_vals[i]
            coin["community_growth"] = g
            if mx_g > mn_g:
                coin["score_community"] = round((g - mn_g) / (mx_g - mn_g) * 100, 1)
            else:
                coin["score_community"] = 50.0
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
            params={"vs_currency": "usd", "days": days, "x_cg_demo_api_key": "CG-hUMjhpbkUbBZ4ohNnXwTeVmd"},
            timeout=30,
            headers={"Accept": "application/json", "User-Agent": "CryptoRadar/1.0"}
        )
        if r.status_code == 200:
            return r.json()
        return {"error": f"API returned {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}

@app.get("/methodology", response_class=HTMLResponse)
async def methodology_page(request: Request):
    return templates.TemplateResponse("methodology.html", {
        "request": request,
    })


@app.get("/trending", response_class=HTMLResponse)
async def trending_page(request: Request):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("SELECT data_json FROM rankings ORDER BY id DESC LIMIT 2")
    rows = c.fetchall()
    conn.close()
    
    coins = []
    if len(rows) >= 2:
        current = json.loads(rows[0][0])
        previous = json.loads(rows[1][0])
        prev_scores = {}
        for coin in previous:
            cid = coin.get("id")
            if cid:
                prev_scores[cid] = float(coin.get("signal_score", 0) or 0)
        for coin in current:
            cid = coin.get("id")
            prev = prev_scores.get(cid, 0)
            curr = float(coin.get("signal_score", 0) or 0)
            coin["score_change"] = round(curr - prev, 1)
        coins = sorted(current, key=lambda x: x.get("score_change", 0), reverse=True)
        
    for i, c in enumerate(coins):
        c["rank"] = i + 1
    
    return templates.TemplateResponse("listing.html", {
        "request": request,
        "coins": coins,
        "page_title": "Trending Cryptocurrencies - Signal Score Gainers | CryptoRadar",
        "page_description": "Cryptocurrencies with the fastest rising signal scores in the last hour. Momentum across Trending, Price, Volume, and Community signals.",
        "page_heading": "Trending",
        "page_subtitle": "Coins with the biggest signal score increase",
        "sort_by": "score_change",
        "sort_label": "Score \u0394",
    })


@app.get("/most-volatile", response_class=HTMLResponse)
async def most_volatile_page(request: Request):
    coins = load_latest_snapshot()
    if not coins:
        coins = []
    coins = sorted(coins, key=lambda x: float(x.get("score_momentum", 0) or 0), reverse=True)
    for i, c in enumerate(coins):
        c["rank"] = i + 1
    return templates.TemplateResponse("listing.html", {
        "request": request,
        "coins": coins,
        "page_title": "Most Volatile Cryptocurrencies - Price Momentum Leaders | CryptoRadar",
        "page_description": "Cryptocurrencies with the highest price volatility and momentum, measured by 7-day sparkline expansion analysis.",
        "page_heading": "Most Volatile",
        "page_subtitle": "Coins with the highest price momentum and volatility expansion",
        "sort_by": "score_momentum",
        "sort_label": "Momentum",
    })



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


@app.get("/daily-post", response_class=HTMLResponse)
async def daily_post_page(request: Request):
    return templates.TemplateResponse("daily_post.html", {"request": request})

@app.get("/api-docs", response_class=HTMLResponse)
async def api_docs_page(request: Request):
    return templates.TemplateResponse("api_docs.html", {"request": request})

@app.get("/compare/{coins:path}", response_class=HTMLResponse)
async def compare_page(request: Request, coins: str):
    snapshot = load_latest_snapshot()
    data = snapshot or []
    parts = coins.split("-vs-")
    c1 = c2 = None
    t1 = parts[0] if len(parts) > 0 else ""
    t2 = parts[1] if len(parts) > 1 else ""
    if data and len(parts) == 2:
        for coin in data:
            if coin.get("id", "").lower() == parts[0].strip().lower():
                c1 = coin
            if coin.get("id", "").lower() == parts[1].strip().lower():
                c2 = coin
    return templates.TemplateResponse("compare.html", {"request": request, "coin1": c1, "coin2": c2, "t1_id": t1, "t2_id": t2})





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

