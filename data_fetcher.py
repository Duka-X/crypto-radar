import requests
import time
from typing import Optional

from reddit_fetcher import RedditFetcher
from trends_fetcher import TrendsFetcher
 

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"


class CoinGeckoFetcher:
    """Fetch trending coins and market data from CoinGecko API."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "CryptoRadar/1.0"
        })
        self.last_call = 0.0
        self.min_interval = 6.0

    def _rate_limit(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

    def _handle_429(self, resp, max_retries=2):
        retries = 0
        while resp.status_code == 429 and retries < max_retries:
            retries += 1
            wait = 15 * retries
            print(f"[CoinGecko] Rate limited, retrying in {wait}s (attempt {retries}/{max_retries})")
            time.sleep(wait)
            return True
        return False

    def get_trending(self) -> list[dict]:
        self._rate_limit()
        try:
            resp = self.session.get(TRENDING_URL, timeout=15)
            if resp.status_code == 429:
                print("[CoinGecko] Trending rate limited, skipping")
                return []
            resp.raise_for_status()
            data = resp.json()
            coins = data.get("coins", [])
            results = []
            for item in coins[:25]:
                c = item.get("item", {})
                coin_id = c.get("id", "")
                symbol = c.get("symbol", "").upper()
                name = c.get("name", "")
                market_cap_rank = c.get("market_cap_rank")
                score = c.get("score", 0)
                thumb = c.get("thumb", "")
                results.append({
                    "id": coin_id,
                    "symbol": symbol,
                    "name": name,
                    "market_cap_rank": market_cap_rank,
                    "trending_score": max(0, 100 - score * 3),
                    "thumb": thumb,
                })
            return results
        except Exception as e:
            print(f"[CoinGecko] Trending fetch error: {e}")
            return []

    def get_prices(self, coin_ids: list[str]) -> dict[str, dict]:
        if not coin_ids:
            return {}
        self._rate_limit()
        ids_param = ",".join(coin_ids)
        try:
            resp = self.session.get(
                f"{COINGECKO_BASE}/coins/markets",
                params={
                    "vs_currency": "usd",
                    "ids": ids_param,
                    "order": "market_cap_desc",
                    "per_page": 50,
                    "page": 1,
                    "sparkline": "true",
                    "price_change_percentage": "1h,24h"
                },
                timeout=15
            )
            if resp.status_code == 429:
                print("[CoinGecko] Prices rate limited, skipping")
                return {}
            resp.raise_for_status()
            markets = resp.json()
            result = {}
            for coin in markets:
                cid = coin.get("id", "")
                result[cid] = {
                    "current_price": coin.get("current_price", 0),
                    "market_cap": coin.get("market_cap", 0),
                    "total_volume": coin.get("total_volume", 0),
                    "price_change_24h": coin.get("price_change_24h", 0),
                    "price_change_percentage_24h": coin.get("price_change_percentage_24h", 0),
                    "sparkline_prices": (coin.get("sparkline_in_7d") or {}).get("price", [])[-24:],
                }
            return result
        except Exception as e:
            print(f"[CoinGecko] Price fetch error: {e}")
            return {}

    def fetch_all(self) -> list[dict]:
        """Fetch trending coins + prices + social signals (Reddit, Google Trends)."""
        trending = self.get_trending()
        if not trending:
            return []
        coin_ids = [c["id"] for c in trending if c["id"]]
        prices = self.get_prices(coin_ids)
        
        # --- Social signals: Reddit mentions ---
        coin_names = [c["name"] for c in trending]
        reddit = RedditFetcher()
        mentions = reddit.fetch_all_mentions(coin_names)
        print(f"[Reddit] Mentions: {dict((k,v) for k,v in mentions.items() if v > 0)}")
        
        # --- Social signals: Google Trends ---
        trends = TrendsFetcher()
        trends_scores = trends.fetch_scores(coin_names)
        print(f"[Trends] Got scores for {sum(1 for v in trends_scores.values() if v > 0)}/{len(trends_scores)} coins")
        
        combined = []
        for coin in trending:
            cid = coin["id"]
            price_info = prices.get(cid, {})
            name = coin["name"]
            reddit_count = mentions.get(name, 0)
            trends_score = trends_scores.get(name, 0)
            combined.append({
                **coin,
                "current_price": price_info.get("current_price", 0),
                "market_cap": price_info.get("market_cap", 0),
                "total_volume": price_info.get("total_volume", 0),
                "price_change_24h": price_info.get("price_change_24h", 0),
                "price_change_percentage_24h": price_info.get("price_change_percentage_24h", 0),
                "sparkline_prices": price_info.get("sparkline_prices", []),
                "reddit_mentions": reddit_count,
                "google_trends_score": trends_score,
            })
        return combined
