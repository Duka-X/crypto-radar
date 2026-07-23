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
        """Fetch Reddit mentions across multiple subreddits and post types.
        Covers 5 subreddits x 2 feed types (hot + new) = 10 sources.
        Returns dict mapping coin name -> total mention count.
        """
        mentions = {c["name"]: 0 for c in coins}
        subs = ["CryptoCurrency", "CryptoMarkets", "SatoshiStreetBets", "altcoin", "CryptoMoonShots"]

        for sub in subs:
            for feed_type in ["hot", "new"]:
                self._rate_limit()
                try:
                    r = self.session.get(
                        f"{self.BASE}/r/{sub}/{feed_type}.json",
                        params={"limit": 100, "raw_json": 1},
                        timeout=10,
                    )
                    if r.status_code == 429:
                        print(f"[Reddit] Rate limited on r/{sub}/{feed_type}, sleeping 60s")
                        time.sleep(60)
                        continue
                    if r.status_code != 200:
                        print(f"[Reddit] r/{sub}/{feed_type} returned {r.status_code}")
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

                            if name.lower() in text:
                                mentions[name] += 1
                                matched += 1
                                matched_this = True

                            if not matched_this and len(symbol) >= 3:
                                if re.search(
                                    r"(?:\$|\\b)" + re.escape(symbol) + r"\\b",
                                    text, re.IGNORECASE
                                ):
                                    mentions[name] += 1
                                    matched += 1

                    print(f"[Reddit] r/{sub}/{feed_type}: {matched} mentions across {len(posts)} posts")

                except Exception as e:
                    print(f"[Reddit] Error fetching r/{sub}/{feed_type}: {e}")

        active = {k: v for k, v in mentions.items() if v > 0}
        print(f"[Reddit] Total coins with mentions: {len(active)}/{len(coins)}")
        return mentions