"""Community signal fetcher: Twitter follower counts and growth rates."""
import json, time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json

DATA_DIR = Path(__file__).parent / "data"
TWITTER_COINS_FILE = DATA_DIR / "twitter_coins.json"
FOLLOWERS_FILE = DATA_DIR / "followers.json"
CONFIG_PATH = Path(__file__).parent / "deploy.json"

def _load_config():
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except:
        pass
    return {}

_config = _load_config()

TWITTER_BEARER = _config.get("twitter", {}).get("bearer_token", "")
COINGECKO_API = "https://api.coingecko.com/api/v3/coins/{coin_id}"
COINGECKO_KEY = _config.get("api", {}).get("coingecko", "")



class CommunityFetcher:
    """Fetch community data (Twitter followers) and compute growth rates."""

    def __init__(self):
        self.twitter_session = requests.Session()
        self.twitter_session.headers.update({
            "Authorization": "Bearer " + TWITTER_BEARER,
            "User-Agent": "CryptoRadar/1.0",
        })
        self.cg_session = requests.Session()
        self.cg_session.headers.update({
            "Accept": "application/json",
            "User-Agent": "CryptoRadar/1.0",
            "x-cg-demo-api-key": COINGECKO_KEY,
        })
        self.last_twitter = 0.0
        self.last_cg = 0.0

    def _rl_twitter(self):
        e = time.time() - self.last_twitter
        if e < 2.0: time.sleep(2.0 - e)
        self.last_twitter = time.time()

    def _rl_cg(self):
        e = time.time() - self.last_cg
        if e < 2.0: time.sleep(2.0 - e)
        self.last_cg = time.time()

    # --- Twitter username cache ---
    def _load_twitter_cache(self) -> dict:
        if TWITTER_COINS_FILE.exists():
            try: return json.loads(TWITTER_COINS_FILE.read_text())
            except: pass
        return {}

    def _save_twitter_cache(self, cache: dict):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TWITTER_COINS_FILE.write_text(json.dumps(cache))

    def get_twitter_username(self, coin_id: str) -> str:
        """Get Twitter username for a coin, from cache or CoinGecko API."""
        cache = self._load_twitter_cache()
        if coin_id in cache:
            return cache[coin_id]
        try:
            self._rl_cg()
            url = COINGECKO_API.format(coin_id=coin_id)
            params = {"localization": "false", "tickers": "false",
                      "community_data": "false", "developer_data": "false"}
            r = self.cg_session.get(url, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                links = data.get("links") or {}
                tw = (links.get("twitter_screen_name") or "").strip()
                cache[coin_id] = tw
                self._save_twitter_cache(cache)
                return tw
        except Exception as e:
            print(f"[Community] CG error for {coin_id}: {e}")
        return ""

    # --- Twitter follower API ---
    def fetch_followers(self, usernames: list[str]) -> dict[str, int]:
        """Fetch follower counts for Twitter usernames. Returns {username: count}."""
        unique = list(set(u.strip() for u in usernames if u.strip()))[:100]
        if not unique:
            return {}
        self._rl_twitter()
        try:
            params = {"usernames": ",".join(unique), "user.fields": "public_metrics"}
            r = self.twitter_session.get("https://api.twitter.com/2/users/by", params=params, timeout=15)
            if r.status_code == 429:
                print("[Community] Rate limited, waiting 60s"); time.sleep(60)
                return self.fetch_followers(usernames)
            r.raise_for_status()
            data = r.json()
            result = {}
            for user in data.get("data", []):
                metrics = user.get("public_metrics", {})
                result[user["username"].lower()] = metrics.get("followers_count", 0)
            return result
        except Exception as e:
            print(f"[Community] Twitter error: {e}")
            return {}

    # --- Snapshot storage ---
    def save_snapshot(self, followers: dict[str, int]):
        try:
            now = datetime.now(timezone.utc).isoformat()
            past = json.loads(FOLLOWERS_FILE.read_text()) if FOLLOWERS_FILE.exists() else []
            past.append({"ts": now, "followers": followers})
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            past = [s for s in past if s["ts"] > cutoff]
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            FOLLOWERS_FILE.write_text(json.dumps(past))
            print(f"[Community] Snapshot saved: {len(followers)} accounts")
        except Exception as e:
            print(f"[Community] Save error: {e}")

    def get_growth_rate(self, twitter_user: str, hours: int = 24) -> float:
        """Get follower growth rate (%) for a Twitter user over the period."""
        try:
            if not FOLLOWERS_FILE.exists(): return 0.0
            all_data = json.loads(FOLLOWERS_FILE.read_text())
            if len(all_data) < 2: return 0.0
            tu = twitter_user.lower()
            current = all_data[-1]["followers"].get(tu, 0)
            if current == 0: return 0.0
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            prev_val = None
            for snap in all_data:
                if snap["ts"] < cutoff and tu in snap["followers"]:
                    prev_val = snap["followers"][tu]
            if prev_val is None or prev_val == 0: return 0.0
            return (current - prev_val) / prev_val * 100
        except Exception as e:
            print(f"[Community] Growth error: {e}")
            return 0.0

    def fetch_twitter_usernames(self, coins: list[dict], limit: int = 5) -> int:
        """Batch fetch Twitter usernames for coins missing from cache."""
        cache = self._load_twitter_cache()
        count = 0
        for c in coins:
            cid = c.get("id", "")
            if not cid or cid in cache:
                continue
            if count >= limit:
                break
            tw = self.get_twitter_username(cid)
            if tw:
                cache[cid] = tw
                self._save_twitter_cache(cache)
                count += 1
        print(f"[Community] Fetched {count} new twitter usernames")
        return count

    def get_current_followers(self, twitter_user: str) -> int:
        try:
            if not FOLLOWERS_FILE.exists(): return 0
            data = json.loads(FOLLOWERS_FILE.read_text())
            if not data: return 0
            return data[-1]["followers"].get(twitter_user.lower(), 0)
        except: return 0

    # --- Main: update community data for coins ---
    def update_for_coins(self, coins: list[dict]) -> dict:
        """Fetch Twitter followers for coins from cache. No slow CG API calls."""
        cache = self._load_twitter_cache()
        twitter_users = []
        coin_twitter = {}
        for c in coins:
            cid = c.get("id", "")
            if not cid: continue
            tw = cache.get(cid, "")
            if tw:
                twitter_users.append(tw)
                coin_twitter[cid] = tw.lower()

        if not twitter_users:
            for c in coins:
                c["community_raw"] = 0
                c["community_growth_24h"] = 0.0
            return {}

        followers = self.fetch_followers(twitter_users)
        if followers:
            self.save_snapshot(followers)

        result = {}
        for c in coins:
            cid = c.get("id", "")
            tw_lower = coin_twitter.get(cid, "")
            if tw_lower and tw_lower in followers:
                growth = self.get_growth_rate(tw_lower, 24)
                count = followers.get(tw_lower, 0)
                c["community_raw"] = count
                c["community_growth_24h"] = round(growth, 2)
                result[cid] = {"growth_24h": growth, "followers": count}
            else:
                c["community_raw"] = 0
                c["community_growth_24h"] = 0.0
        print(f"[Community] Updated {len(result)} coins")
        return result

        result = {}
        for c in coins:
            cid = c.get("id", "")
            tw_lower = coin_twitter.get(cid, "")
            if tw_lower and tw_lower in followers:
                growth_24h = self.get_growth_rate(tw_lower, 24)
                f_count = followers.get(tw_lower, 0)
                c["community_raw"] = f_count
                c["community_growth_24h"] = round(growth_24h, 2)
                result[cid] = {"growth_24h": growth_24h, "followers": f_count}
            else:
                c["community_raw"] = 0
                c["community_growth_24h"] = 0.0
        print(f"[Community] Updated {len(result)} coins")
        return result
