import math, json, threading, random
import os
import requests, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from reddit_fetcher import RedditFetcher

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
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
        if not MENTIONS_FILE.exists(): return 0
        data = json.loads(MENTIONS_FILE.read_text())
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        recent = [r for r in data if r["ts"] > cutoff]
        total = sum(r["mentions"].get(coin_name, 0) for r in recent)
        return total
    except Exception:
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


class CoinGeckoFetcher:
    """Mixed-source fetcher: trending + top gainers + small-cap high-volume."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "Accept": "application/json",
            "User-Agent": "CryptoRadar/1.0",
        })
        self.api_key = self._load_api_key()
        if self.api_key:
            self.s.headers["x-cg-demo-api-key"] = self.api_key
        self.last = time.time()

    def _load_api_key(self):
        try:
            cfg = Path(__file__).parent / "deploy.json"
            if cfg.exists():
                data = json.loads(cfg.read_text())
                return data.get("api", {}).get("coingecko", "")
        except Exception:
            pass
        return os.environ.get("CG_API_KEY", "")

    def _rl(self):
        elapsed = time.time() - self.last
        if elapsed < 25.0: time.sleep(25.0 - elapsed)
        self.last = time.time()

    def _get(self, url, params=None, _retries=0):
        self._rl()
        try:
            if params is None: params = {}
            params["x_cg_demo_api_key"] = self.api_key
            r = self.s.get(url, params=params, timeout=45)
            if r.status_code == 429 and _retries < 3:
                wait = 60 + _retries * 30
                print(f"[CG] Rate limited ({_retries+1}/3), sleeping {wait}s")
                time.sleep(wait)
                return self._get(url, params, _retries + 1)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"[CG] {url[:60]}: {e}")
            return None

    def _normalize(self, coin):
        sp7 = (coin.get("sparkline_in_7d") or {}).get("price", [])
        rank = coin.get("market_cap_rank") or 999
        return {
            "id": coin.get("id", ""),
            "symbol": coin.get("symbol", "").upper(),
            "name": coin.get("name", ""),
            "market_cap_rank": rank,
            "trending_score": max(0, 100 - rank),
            "thumb": coin.get("image", ""),
            "current_price": coin.get("current_price", 0),
            "market_cap": coin.get("market_cap", 0),
            "total_volume": coin.get("total_volume", 0),
            "price_change_percentage_24h": coin.get("price_change_percentage_24h", 0),
            "sparkline_full": sp7,
            "sparkline_prices": sp7[-24:] if sp7 else [],
            "momentum_score": _vol_expand(sp7),
            "community_score": self._community_by_mcap(coin),
            "community_raw": 0,
            "reddit_mentions": 0,
        }

    def _community_by_mcap(self, coin):
        mcap = coin.get("market_cap") or 0
        if mcap > 1e10: return 0.8
        if mcap > 1e9: return 0.65
        if mcap > 1e8: return 0.5
        if mcap > 1e7: return 0.35
        return 0.2

    def _trending_ids(self):
        data = self._get(TRENDING_URL)
        if not data: return []
        out = []
        for item in (data.get("coins") or [])[:25]:
            c = item.get("item") or {}
            out.append({
                "id": c.get("id", ""),
                "symbol": c.get("symbol", "").upper(),
                "name": c.get("name", ""),
                "market_cap_rank": c.get("market_cap_rank") or 999,
                "thumb": c.get("thumb", ""),
            })
        return out

    def _fetch_by_ids(self, ids):
        if not ids: return []
        data = self._get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd",
            "ids": ",".join(ids),
            "order": "market_cap_desc",
            "per_page": len(ids),
            "sparkline": "true",
            "price_change_percentage": "24h",
        })
        return [self._normalize(c) for c in data] if data else []

    def _fetch_market_page(self, order="volume_desc", per_page=250):
        data = self._get(f"{COINGECKO_BASE}/coins/markets", params={
            "vs_currency": "usd",
            "order": order,
            "per_page": min(per_page, 250),
            "sparkline": "true",
            "price_change_percentage": "24h",
        })
        return [self._normalize(c) for c in data] if data else []

    def fetch_all(self):
        """Mixed-source: trending + top gainers + small-cap high-volume.
        Priority: trending -> top gainers -> small-cap high-vol -> fill by volume.
        """
        # 1. Trending
        trending_basic = self._trending_ids()  # returns list with score field
        trending_ids = [c["id"] for c in trending_basic if c["id"]]
        print(f"[CG] Trending IDs: {len(trending_ids)}")

        trending_full = self._fetch_by_ids(trending_ids)
        trending_found = {c["id"] for c in trending_full}
        print(f"[CG] Trending with market data: {len(trending_full)}")

        # 2. Broad market
        market_coins = self._fetch_market_page("volume_desc", 250)
        print(f"[CG] Market coins: {len(market_coins)}")

        # 3. Select & deduplicate
        selected = []
        seen = set()

        def _add(coin):
            cid = coin.get("id", "")
            if cid and cid not in seen:
                selected.append(coin)
                seen.add(cid)

        for coin in trending_full:
            _add(coin)

        if len(selected) < 100:
            gainers = sorted(
                [c for c in market_coins if c.get("id") not in seen],
                key=lambda x: x.get("price_change_percentage_24h") or -999,
                reverse=True,
            )
            for coin in gainers:
                if len(selected) >= 100: break
                _add(coin)

        if len(selected) < 100:
            small_cap = sorted(
                [c for c in market_coins
                 if c.get("id") not in seen and (c.get("market_cap") or 1e12) < 200_000_000],
                key=lambda x: x.get("total_volume") or 0,
                reverse=True,
            )
            for coin in small_cap:
                if len(selected) >= 100: break
                _add(coin)

        if len(selected) < 100:
            for coin in market_coins:
                if len(selected) >= 100: break
                _add(coin)

        # 4. Reddit
        try:
            reddit = RedditFetcher()
            coin_names = [c.get("name", "") for c in selected if c.get("name")]
            mentions = reddit.fetch_all_mentions(coin_names)
            _save_mention_snapshot(mentions)
            for c in selected:
                c["reddit_mentions"] = mentions.get(c.get("name", ""), 0)
        except Exception as e:
            print(f"[Reddit] Error: {e}")

                # Override trending_score for trending coins (position-based)
        if trending_basic:
            n = len(trending_basic)
            if n > 1:
                for pos, tb in enumerate(trending_basic):
                    for coin in selected:
                        if coin.get("id") == tb.get("id"):
                            coin["trending_score"] = max(5, round(100 - pos * (95 / (n - 1))))
                            break

        # Community data (Twitter followers)
        try:
            CommunityFetcher().update_for_coins(selected)
        except Exception as e:
            print(f"[Community] Error: {e}")

        print(f"[CG] Final selection: {len(selected)} coins")
        return selected

    def fetch_top_prices(self, n=100):
        """Alias for backward compatibility."""
        return self.fetch_all()[:n]
    def fetch_community_data(self, ids):
        out = {}
        lock = threading.Lock()
        def _fetch_one(cid):
            try:
                r = self.s.get(
                    f"{COINGECKO_BASE}/coins/{cid}",
                    params={"localization": "false", "tickers": "false",
                            "market_data": "false", "community_data": "true",
                            "developer_data": "false", "sparkline": "false"},
                    timeout=15,
                )
                if r.status_code == 200:
                    cd = (r.json().get("community_data") or {}) or {}
                    with lock:
                        out[cid] = {
                            "twitter": float(cd.get("twitter_followers", 0) or 0),
                            "telegram": float(cd.get("telegram_channel_user_count", 0) or 0),
                            "reddit": float(cd.get("reddit_subscribers", 0) or 0),
                        }
            except Exception as e:
                print(f"[CG] Community {cid}: {e}")
        for i in range(0, len(ids), 4):
            batch = ids[i:i+4]
            with ThreadPoolExecutor(max_workers=4) as pool:
                pool.map(_fetch_one, batch)
            if i + 4 < len(ids):
                self._rl()
        print(f"[CG] Community data: {len(out)}/{len(ids)}")
        return out
