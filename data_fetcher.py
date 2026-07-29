import math, json, threading, random
import os
import requests, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from reddit_fetcher import RedditFetcher
from community_fetcher import CommunityFetcher

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
BINANCE_BASE = "https://api.binance.com"
COINGECKO_COINLIST_URL = f"{COINGECKO_BASE}/coins/list"
MENTIONS_FILE = Path(__file__).parent / "data" / "reddit_mentions.json"
COIN_LIST_CACHE_FILE = Path(__file__).parent / "data" / "coin_list_cache.json"


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
    _coin_list = {}
    _coin_list_lock = threading.Lock()
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

    def _get_coin_list(self):
        """Fetch CoinGecko coin list for symbol->id mapping (cached, thread-safe)."""
        if CoinGeckoFetcher._coin_list:
            return CoinGeckoFetcher._coin_list
        with CoinGeckoFetcher._coin_list_lock:
            if CoinGeckoFetcher._coin_list:
                return CoinGeckoFetcher._coin_list
            # File cache fallback (survives restart & API outage)
            if COIN_LIST_CACHE_FILE.exists():
                try:
                    data = json.loads(COIN_LIST_CACHE_FILE.read_text())
                    if data and isinstance(data, dict) and len(data) > 1000:
                        CoinGeckoFetcher._coin_list = data
                        print(f"[CG] Coin list from cache: {len(data)} symbols")
                        return data
                except Exception:
                    pass

            self._rl()
            try:
                r = self.s.get(COINGECKO_COINLIST_URL, timeout=30)
                r.raise_for_status()
                raw = r.json()
                lookup = {}
                for c in raw:
                    sym = c.get("symbol", "").lower()
                    if sym and c.get("id"):
                        if sym not in lookup:
                            lookup[sym] = {"id": c["id"], "name": c.get("name", ""), "symbol": sym.upper()}
                for sym, cid in {"btc":"bitcoin","eth":"ethereum","bnb":"binancecoin","sol":"solana","xrp":"ripple","doge":"dogecoin","ada":"cardano","avax":"avalanche-2","dot":"polkadot","matic":"polygon","link":"chainlink","uni":"uniswap","atom":"cosmos","ltc":"litecoin","bch":"bitcoin-cash","trx":"tron","fil":"filecoin","apt":"aptos","sui":"sui","op":"optimism","arb":"arbitrum","pepe":"pepe","inj":"injective-protocol"}.items():
                    if sym in lookup and lookup[sym]["id"] != cid:
                        found = [c for c in raw if c.get("id") == cid]
                        if found:
                            lookup[sym] = {"id": cid, "name": found[0].get("name", ""), "symbol": sym.upper()}
                CoinGeckoFetcher._coin_list = lookup
                try:
                    COIN_LIST_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    COIN_LIST_CACHE_FILE.write_text(json.dumps(lookup))
                except Exception:
                    pass
                print(f"[CG] Coin list: {len(lookup)} symbols")
                return lookup
            except Exception as e:
                print(f"[CG] Coin list error: {e}")
                return {}


    def _get_volume_growth(self, binance_data):
        """Fetch 1h K-line volumes, compare vs 24h average hourly volume."""
        symbols = list(binance_data.keys())
        if not symbols:
            return {}

        result = {}
        lock = threading.Lock()
        now = datetime.now(timezone.utc)

        def fetch_kline(sym):
            try:
                r = requests.get(
                    f"{BINANCE_BASE}/api/v3/klines",
                    params={"symbol": sym.upper() + "USDT", "interval": "1h", "limit": 25},
                    timeout=15
                )
                r.raise_for_status()
                data = r.json()
                if not data or len(data) < 2:
                    return None

                # data[-1] = current in-progress kline
                # data[:-1] = 24 completed klines (24h baseline)
                curr = data[-1]

                # Average hourly volume over last 24 completed hours
                completed_vols = [float(k[7]) for k in data[:-1] if float(k[7]) > 0]
                if not completed_vols:
                    return sym, 1.0
                baseline = sum(completed_vols) / len(completed_vols)

                # Current hour volume
                curr_vol = float(curr[7])
                kline_open = datetime.fromtimestamp(curr[0] / 1000, tz=timezone.utc)
                mins_past = max((now - kline_open).total_seconds() / 60, 0.1)

                # Estimate full-hour volume, or use raw value in first 5min
                if mins_past >= 5:
                    estimated_curr = curr_vol * (60.0 / mins_past)
                else:
                    estimated_curr = curr_vol

                if baseline > 0 and estimated_curr > 0:
                    g = estimated_curr / baseline
                elif estimated_curr > 0:
                    g = 1.0
                else:
                    g = 0

                return sym, min(g, 50.0)
            except Exception as e:
                return None

        with ThreadPoolExecutor(max_workers=30) as pool:
            futures = [pool.submit(fetch_kline, sym) for sym in symbols]
            for f in futures:
                res = f.result()
                if res:
                    result[res[0]] = res[1]

        print(f"[Volume] 1h kline vs 24h avg: {len(result)} symbols")
        return result


    def _fetch_binance_tickers(self):
        """Fetch all USDT tickers from Binance (free, 1200 req/min)."""
        try:
            r = requests.get(f"{BINANCE_BASE}/api/v3/ticker/24hr", timeout=30)
            r.raise_for_status()
            data = r.json()
            pairs = {}
            for t in data:
                sym = t.get("symbol", "")
                if sym.endswith("USDT"):
                    base = sym[:-4].lower()
                    pairs[base] = {
                        "price": float(t.get("lastPrice", 0) or 0),
                        "volume": float(t.get("quoteVolume", 0) or 0),
                        "change": float(t.get("priceChangePercent", 0) or 0),
                    }
            print(f"[Binance] USDT pairs: {len(pairs)}")
            return pairs
        except Exception as e:
            print(f"[Binance] Error: {e}")
            return {}

    def _fetch_coin_markets(self, ids):
        self._rl()
        try:
            params = {
                "vs_currency": "usd",
                "ids": ",".join(ids),
                "order": "market_cap_desc",
                "per_page": len(ids),
                "sparkline": "true",
                "price_change_percentage": "24h",
            }
            params["x_cg_demo_api_key"] = self.api_key
            r = self.s.get(f"{COINGECKO_BASE}/coins/markets", params=params, timeout=45)
            r.raise_for_status()
            data = r.json()
            result = {}
            for coin in data:
                cid = coin.get("id", "")
                sp7 = (coin.get("sparkline_in_7d") or {}).get("price", [])
                result[cid] = {
                    "thumb": coin.get("image", ""),
                    "current_price": coin.get("current_price", 0),
                    "total_volume": coin.get("total_volume", 0),
                    "price_change_percentage_24h": coin.get("price_change_percentage_24h", 0),
                    "market_cap": coin.get("market_cap", 0),
                    "sparkline_full": sp7,
                    "sparkline_prices": sp7[-24:] if sp7 else [],
                }
            print(f"[CG] Markets enriched: {len(result)} coins")
            return result
        except Exception as e:
            print(f"[CG] Markets fetch error: {e}")
            return {}

    def _symbol_thumb(self, sym):
        return f"https://bin.bnbstatic.com/static/images/coin/{sym.upper()}.png"

    def _estimate_mcap(self, price, vol):
        if price > 0 and vol > 0:
            return vol * 10
        return 0

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
        """Mixed-source: trending (CG) + prices (Binance) + volume growth."""
        coin_list = self._get_coin_list()

        trending_basic = self._trending_ids()
        print(f"[CG] Trending IDs: {len(trending_basic)}")

        binance = self._fetch_binance_tickers()

        # Volume growth
        growth = self._get_volume_growth(binance)

        candidates = []
        seen = set()

        # Priority 1: trending (use Binance data + growth)
        if trending_basic:
            n = len(trending_basic)
            for pos, tb in enumerate(trending_basic):
                cid = tb.get("id", "")
                sym = (tb.get("symbol") or "").lower()
                if not cid:
                    continue
                bd = binance.get(sym)
                coin = {
                    "id": cid,
                    "symbol": sym.upper(),
                    "name": tb.get("name", ""),
                    "market_cap_rank": 999,
                    "trending_score": max(5, round(100 - pos * (95 / max(n-1, 1)))),
                    "thumb": tb.get("thumb", self._symbol_thumb(sym)),
                    "current_price": bd["price"] if bd else 0,
                    "total_volume": bd["volume"] if bd else 0,
                    "price_change_percentage_24h": bd["change"] if bd else 0,
                    "market_cap": self._estimate_mcap(bd["price"] if bd else 0, bd["volume"] if bd else 0),
                    "volume_growth": growth.get(sym, 1.0) if bd and growth.get(sym, 0) > 0 else 1.0,
                    "sparkline_full": [],
                    "sparkline_prices": [],
                    "momentum_score": 0,
                    "community_score": 0,
                    "community_raw": 0,
                    "reddit_mentions": 0,
                }
                candidates.append(coin)
                seen.add(cid)

        # Priority 2: fill by volume growth descending
        scored = [(base_sym, bd, growth.get(base_sym, 1.0)) for base_sym, bd in binance.items()]
        scored.sort(key=lambda x: x[2], reverse=True)

        for base_sym, bd, gr in scored:
            if len(candidates) >= 100:
                break
            cl = coin_list.get(base_sym)
            if not cl:
                continue
            cid = cl["id"]
            if cid in seen:
                continue
            cap = self._estimate_mcap(bd["price"], bd["volume"])
            coin = {
                "id": cid,
                "symbol": base_sym.upper(),
                "name": cl["name"] if cl else base_sym.upper(),
                "market_cap_rank": len(candidates) + 1,
                "trending_score": max(0, 80 - len(candidates)),
                "thumb": self._symbol_thumb(base_sym),
                "volume_growth": gr,
                "current_price": bd["price"],
                "total_volume": bd["volume"],
                "price_change_percentage_24h": bd["change"],
                "market_cap": cap,
                "sparkline_full": [],
                "sparkline_prices": [],
                "momentum_score": 0,
                "community_score": self._community_by_mcap({"market_cap": cap}),
                "community_raw": 0,
                "reddit_mentions": 0,
            }
            candidates.append(coin)
            seen.add(cid)

        enrich_ids = [c["id"] for c in candidates]
        if enrich_ids:
            enrich_data = self._fetch_coin_markets(enrich_ids)
            for c in candidates:
                e = enrich_data.get(c["id"])
                if e:
                    c["thumb"] = e["thumb"]
                    if not c.get("current_price"):
                        c["current_price"] = e.get("current_price", 0)
                    if not c.get("total_volume"):
                        c["total_volume"] = e.get("total_volume", 0)
                    if not c.get("price_change_percentage_24h"):
                        c["price_change_percentage_24h"] = e.get("price_change_percentage_24h", 0)
                    if not c.get("market_cap"):
                        c["market_cap"] = e.get("market_cap", 0)
                    c["sparkline_full"] = e["sparkline_full"]
                    c["sparkline_prices"] = e["sparkline_prices"]

        # Reddit mentions
        try:
            reddit = RedditFetcher()
            names = [c.get("name", "") for c in candidates if c.get("name")]
            mentions = reddit.fetch_all_mentions(names)
            _save_mention_snapshot(mentions)
            for c in candidates:
                c["reddit_mentions"] = mentions.get(c.get("name", ""), 0)
        except Exception as e:
            print(f"[Reddit] Error: {e}")

        # Community data (Twitter followers)
        try:
            CommunityFetcher().update_for_coins(candidates)
        except Exception as e:
            print(f"[Community] Error: {e}")

        print(f"[Fetch] Final: {len(candidates)} coins")
        return candidates
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
