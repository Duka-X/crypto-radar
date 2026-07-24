import requests, time

class RedditFetcher:
    BASE = "https://www.reddit.com"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "CryptoRadar/1.0"})

    def fetch_all_mentions(self, coin_names):
        mentions = {n: 0 for n in coin_names}
        subs = ["CryptoCurrency", "CryptoMarkets", "SatoshiStreetBets"]
        for sub in subs:
            try:
                r = self.session.get(f"{self.BASE}/r/{sub}/hot.json", params={"limit": 100}, timeout=10)
                if r.status_code == 200:
                    for post in r.json().get("data", {}).get("children", []):
                        t = (post["data"].get("title","") + " " + post["data"].get("selftext","")).lower()
                        for c in coin_names:
                            if c.lower() in t: mentions[c] += 1
                time.sleep(1)
            except: pass
        return mentions
