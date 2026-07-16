import requests, time
import re

class RedditFetcher:
    BASE = "https://www.reddit.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.last_call = 0.0
        self.min_interval = 1.5

    def _rate_limit(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

    def fetch_mentions(self, coins: list[dict]) -> dict[str, int]:
        """Fetch Reddit mentions for each coin.
        
        coins: list of dicts with at least "name" and "symbol" keys.
        Returns dict mapping coin name -> mention count across posts.
        Matches both full name (substring) and ticker symbol (word-boundary regex).
        """
        mentions = {c["name"]: 0 for c in coins}
        subs = ["CryptoCurrency", "CryptoMarkets", "SatoshiStreetBets"]
        
        for sub in subs:
            self._rate_limit()
            try:
                r = self.session.get(
                    f"{self.BASE}/r/{sub}/hot.json",
                    params={"limit": 100, "raw_json": 1},
                    timeout=10,
                )
                if r.status_code == 429:
                    print(f"[Reddit] Rate limited on r/{sub}, sleeping 60s")
                    time.sleep(60)
                    continue
                if r.status_code != 200:
                    print(f"[Reddit] r/{sub} returned {r.status_code}")
                    continue
                
                posts = r.json().get("data", {}).get("children", [])
                matched = 0
                for post in posts:
                    post_data = post.get("data", {})
                    text = (
                        (post_data.get("title", "") or "") + " " +
                        (post_data.get("selftext", "") or "")
                    ).lower()
                    
                    for c in coins:
                        name = c["name"]
                        symbol = c.get("symbol", "").upper()
                        matched_this = False
                        
                        # Match by full name (substring, e.g. "bitcoin" matches "bitcoin")
                        if name.lower() in text:
                            mentions[name] += 1
                            matched += 1
                            matched_this = True
                        
                        # Match by symbol (word-boundary, e.g. "BTC", "$BTC")
                        if not matched_this and len(symbol) >= 3:
                            if re.search(
                                r"(?:\$|\b)" + re.escape(symbol) + r"\b",
                                text, re.IGNORECASE
                            ):
                                mentions[name] += 1
                                matched += 1
                
                print(f"[Reddit] r/{sub}: {matched} mentions across {len(posts)} posts")
                    
            except Exception as e:
                print(f"[Reddit] Error fetching r/{sub}: {e}")
        
        active = {k: v for k, v in mentions.items() if v > 0}
        print(f"[Reddit] Total coins with mentions: {len(active)}/{len(coins)}")
        return mentions
