import math, json, threading
import requests, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from reddit_fetcher import RedditFetcher

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

MENTIONS_FILE = Path(__file__).parent / "data" / "reddit_mentions.json"


def _save_mention_snapshot(mention_counts):
    try:
        now = datetime.now(timezone.utc).isoformat()
        if MENTIONS_FILE.exists():
            data = json.loads(MENTIONS_FILE.read_text())
        else:
            data = []
        data.append({"ts": now, "mentions": mention_counts})
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        data = [r for r in data if r["ts"] > cutoff]
        MENTIONS_FILE.write_text(json.dumps(data))
    except Exception as e:
        print(f"[Mentions] Save error: {e}")


def _get_rolling_24h(coin_name):
    try:
        if not MENTIONS_FILE.exists():
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        data = json.loads(MENTIONS_FILE.read_text())
        total = 0
        for record in data:
            if record["ts"] > cutoff:
                total += record["mentions"].get(coin_name, 0)
        return total
    except:
        return 0


def _vol_expand(sp):
    if not sp or len(sp) < 48: return 0.0
    cs = len(sp) // 7
    days = []
    for i in range(7):
        seg = sp[i*cs:(i+1)*cs]
        if len(seg) >= 2:
            mn, mx, avg = min(seg), max(seg), sum(seg)/len(seg)
            if avg > 0: days.append((mx - mn) / avg)
    if len(days) < 2: return 0.0
    cur, basev = days[-1], sum(days[:-1]) / len(days[:-1])
    return min(500.0, cur / basev * 100) if basev > 0 else 0.0


def _community_score(data):
    """Social media score from CoinGecko community_data (Twitter, Telegram, Reddit)."""
    cd = (data.get("community_data") or {}) or {}
    tf = float(cd.get("twitter_followers", 0) or 0)
    tg = float(cd.get("telegram_channel_user_count", 0) or 0)
    rs = float(cd.get("reddit_subscribers", 0) or 0)
    return math.log(1 + tf) * 0.05 + math.log(1 + tg) * 0.08 + math.log(1 + rs) * 0.1

def _dev_score(data):
    dd = (data.get("developer_data") or {}) or {}
    commits = float(dd.get("commit_count_4_weeks", 0) or 0)
    code = dd.get("code_additions_deletions_4_weeks") or {}
    if isinstance(code, dict):
        a = float(code.get("additions", 0) or 0)
        d = float(code.get("deletions", 0) or 0)
        code_churn = abs(a) + abs(d)
    else:
        code_churn = float(code or 0)
    score = commits * 5 + code_churn * 0.005
    if score > 0:
        score = math.log(1 + score / 10) * 10
    return score

class CoinGeckoFetcher:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json", "User-Agent": "CryptoRadar/1.0"})
        self.last = 0.0
    def _rl(self):
        e = time.time() - self.last
        if e < 6.0: time.sleep(6.0 - e)
        self.last = time.time()
    def get_trending(self):
        self._rl()
        try:
            r = self.s.get(f"{COINGECKO_BASE}/search/trending", timeout=15)
            if r.status_code == 429: return []
            r.raise_for_status()
            out = []
            for item in (r.json().get("coins") or [])[:25]:
                c = item.get("item", {})
                out.append({"id": c.get("id",""), "symbol": c.get("symbol","").upper(), "name": c.get("name",""),
                    "market_cap_rank": c.get("market_cap_rank"), "trending_score": max(0, 100 - (c.get("score",0) or 0) * 3),
                    "thumb": c.get("thumb","")})
            return out
        except Exception as e: print(f"[CG] Trending: {e}"); return []
    def get_prices(self, ids):
        if not ids: return {}
        self._rl()
        try:
            r = self.s.get(f"{COINGECKO_BASE}/coins/markets", params={
                "vs_currency": "usd", "ids": ",".join(ids),
                "order": "market_cap_desc", "per_page": 50, "page": 1,
                "sparkline": "true", "price_change_percentage": "24h"}, timeout=15)
            if r.status_code == 429: return {}
            r.raise_for_status()
            out = {}
            for coin in r.json():
                cid = coin.get("id","")
                sp7 = (coin.get("sparkline_in_7d") or {}).get("price", [])
                out[cid] = {"current_price": coin.get("current_price",0), "market_cap": coin.get("market_cap",0),
                    "total_volume": coin.get("total_volume",0),
                    "price_change_percentage_24h": coin.get("price_change_percentage_24h",0),
                    "sparkline_full": sp7, "sparkline_prices": sp7[-24:] if sp7 else []}
            return out
        except Exception as e: print(f"[CG] Prices: {e}"); return {}
    def get_dev_data(self, ids):
        """Fetch developer + community data in parallel batches (4 per batch)."""
        out = {}
        lock = threading.Lock()
        def _fetch_one(cid):
            try:
                r = self.s.get(f"{COINGECKO_BASE}/coins/{cid}", params={
                    "localization":"false", "tickers":"false", "market_data":"false",
                    "community_data":"true", "developer_data":"true", "sparkline":"false"}, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    with lock:
                        out[cid] = {"community_score": _dev_score(data), "community_raw": _community_score(data)}
                else:
                    print(f"[CG] Dev {cid}: {r.status_code}")
            except Exception as e:
                print(f"[CG] Dev {cid}: {e}")
        for i in range(0, len(ids), 4):
            batch = ids[i:i+4]
            with ThreadPoolExecutor(max_workers=4) as pool:
                pool.map(_fetch_one, batch)
            if i + 4 < len(ids):
                self._rl()
        print(f"[CG] Dev+Community: {len(out)}/{len(ids)}")
        return out
        trend = self.get_trending()
        if not trend: return []
        ids = [c["id"] for c in trend if c["id"]]
        prices = self.get_prices(ids)
        dev = self.get_dev_data(ids)
        # Reddit mentions
        try:
            reddit = RedditFetcher()
            reddit_count = reddit.fetch_mentions(trend)
            _save_mention_snapshot(reddit_count)
        except Exception as e:
            print(f"[Reddit] Error: {e}")
            reddit_count = {}

        out = []
        for coin in trend:
            cid = coin["id"]
            pi = prices.get(cid, {})
            out.append({**coin,
                "current_price": pi.get("current_price",0), "market_cap": pi.get("market_cap",0),
                "total_volume": pi.get("total_volume",0),
                "price_change_percentage_24h": pi.get("price_change_percentage_24h",0),
               "sparkline_prices": pi.get("sparkline_prices",[]),
                "sparkline_full": pi.get("sparkline_full",[]),
                "momentum_score": _vol_expand(pi.get("sparkline_full",[])),
                "community_score": dev.get(cid, {}).get("community_score",0),
                "community_raw": dev.get(cid, {}).get("community_raw",0),
                "reddit_mentions": 0})
        return out