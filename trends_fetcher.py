import requests
import time
import json
import re


class TrendsFetcher:
    """Fetch Google Trends interest scores for crypto coin names.

    Uses the unofficial Google Trends API (same endpoints pytrends uses).
    Gracefully falls back to score 0 when data is unavailable.
    """

    BASE = "https://trends.google.com/trends/api"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.last_call = 0.0
        self.min_interval = 2.0
        self._got_cookies = False

    def _rate_limit(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

    def _get_cookies(self):
        """Fetch the landing page to obtain NID cookie required by the API."""
        if self._got_cookies:
            return True
        try:
            resp = self.session.get(
                "https://trends.google.com/trends/?geo=US&hl=en-US",
                timeout=10,
            )
            resp.raise_for_status()
            self._got_cookies = True
            return True
        except Exception as e:
            print(f"[Trends] Cookie fetch failed: {e}")
            return False

    def _explore(self, keywords: list[str]) -> dict | None:
        """Call /explore to get a widget token for interest-over-time data."""
        if not keywords:
            return None
        self._rate_limit()
        req_data = {
            "comparisonItem": [
                {"keyword": kw, "geo": "", "time": "now 7-d"}
                for kw in keywords
            ],
            "category": 0,
            "property": "",
        }
        params = {
            "hl": "en-US",
            "tz": "-480",
            "req": json.dumps(req_data),
        }
        try:
            resp = self.session.get(
                f"{self.BASE}/explore",
                params=params,
                timeout=15,
            )
            if resp.status_code == 429:
                print("[Trends] Rate limited, skipping")
                return None
            resp.raise_for_status()
            text = resp.text
            # API returns JSON with a leading )]}',\n
            if text.startswith(")]}'"):
                text = text[5:].lstrip()
            return json.loads(text)
        except Exception as e:
            print(f"[Trends] Explore error: {e}")
            return None

    def _get_interest(self, token: str) -> list[dict] | None:
        """Call widgetdata/multiline to get actual interest values."""
        self._rate_limit()
        params = {
            "hl": "en-US",
            "tz": "-480",
            "req": json.dumps({"token": token, "language": "en-US", "country": "US"}),
        }
        try:
            resp = self.session.get(
                f"{self.BASE}/widgetdata/multiline",
                params=params,
                timeout=15,
            )
            if resp.status_code == 429:
                print("[Trends] Widget rate limited, skipping")
                return None
            resp.raise_for_status()
            text = resp.text
            if text.startswith(")]}'"):
                text = text[5:].lstrip()
            data = json.loads(text)
            return data.get("default", {}).get("timelineData", [])
        except Exception as e:
            print(f"[Trends] Widget error: {e}")
            return None

    def fetch_scores(self, coin_names: list[str]) -> dict[str, float]:
        """Return dict {coin_name: interest_score_0_100} for matching coins.

        Batches names up to 5 per API call (Google Trends allows 5 terms
        in a single comparison widget).
        """
        if not coin_names:
            return {}

        if not self._get_cookies():
            return {n: 0.0 for n in coin_names}

        # Deduplicate and filter
        unique = list(dict.fromkeys(coin_names))
        # Simple sanitization: strip non-alphanumeric except spaces
        sanitized = [re.sub(r"[^a-zA-Z0-9\s]", "", n).strip() for n in unique]
        sanitized = [s for s in sanitized if s]

        scores: dict[str, float] = {}
        batch_size = 5

        for i in range(0, len(sanitized), batch_size):
            batch = sanitized[i : i + batch_size]
            explore_data = self._explore(batch)
            if not explore_data:
                for name in batch:
                    scores[name] = 0.0
                continue

            # Find the interest-over-time widget
            widgets = explore_data.get("widgets", [])
            widget_token = None
            for w in widgets:
                if w.get("id") == "TIMESERIES":
                    widget_token = w.get("token")
                    break

            if not widget_token:
                for name in batch:
                    scores[name] = 0.0
                continue

            timeline = self._get_interest(widget_token)
            if not timeline:
                for name in batch:
                    scores[name] = 0.0
                continue

            # Each timeline entry has "value" as [intensity]
            # Aggregate: average of last-24h values, then bucket per keyword
            # The API returns one line per keyword, each with time-series values
            # We'll compute average interest per keyword from the timeline
            # keywords match the order in the comparisonItem
            kw_map = {kw: [] for kw in batch}
            for entry in timeline:
                values = entry.get("value", [])
                for idx, val in enumerate(values):
                    if idx < len(batch):
                        kw_map[batch[idx]].append(val)

            for name, vals in kw_map.items():
                if vals:
                    # Average and normalize
                    avg = sum(vals) / len(vals)
                    scores[name] = min(100.0, round(avg, 1))
                else:
                    scores[name] = 0.0

            # Brief pause between batches
            time.sleep(1.0)

        # Map back to original names
        result = {}
        for name in coin_names:
            key = re.sub(r"[^a-zA-Z0-9\s]", "", name).strip()
            result[name] = scores.get(key, 0.0)

        return result
