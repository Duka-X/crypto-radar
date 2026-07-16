import math
import requests, time

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

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
        out = {}
        for cid in ids:
            self._rl()
            try:
                r = self.s.get(f"{COINGECKO_BASE}/coins/{cid}", params={
                    "localization":"false", "tickers":"false", "market_data":"false",
                    "community_data":"true", "developer_data":"true", "sparkline":"false"}, timeout=15)
                if r.status_code == 200: out[cid] = {"community_score": _dev_score(r.json())}
                else: print(f"[CG] Dev {cid}: {r.status_code}")
            except Exception as e: print(f"[CG] Dev {cid}: {e}")
        print(f"[CG] Dev: {len(out)}/{len(ids)}")
        return out
    def fetch_all(self):
        trend = self.get_trending()
        if not trend: return []
        ids = [c["id"] for c in trend if c["id"]]
        prices = self.get_prices(ids)
        dev = self.get_dev_data(ids)
        out = []
        for coin in trend:
            cid = coin["id"]
            pi = prices.get(cid, {})
            out.append({**coin,
                "current_price": pi.get("current_price",0), "market_cap": pi.get("market_cap",0),
                "total_volume": pi.get("total_volume",0),
                "price_change_percentage_24h": pi.get("price_change_percentage_24h",0),
                "sparkline_prices": pi.get("sparkline_prices",[]),
                "momentum_score": _vol_expand(pi.get("sparkline_full",[])),
                "community_score": dev.get(cid, {}).get("community_score",0)})
        return out
