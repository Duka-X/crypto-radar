from typing import Optional


class SignalScorer:
    """Rank coins by multi-signal score."""

    WEIGHTS = {
        "trending_score": 0.50,
        "price_change_24h": 0.25,
        "volume_score": 0.25,
    }

    def _normalize(self, values: list[float]) -> list[float]:
        """Min-max normalize a list of values to 0-100."""
        if not values:
            return []
        mn = min(values)
        mx = max(values)
        if mx == mn:
            return [50.0] * len(values)
        return [(v - mn) / (mx - mn) * 100 for v in values]

    def _safe_float(self, v) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def score(self, coins: list[dict]) -> list[dict]:
        """
        Take raw coins (from CoinGecko fetcher) and return scored/ranked list.
        Adds 'signal_score' and 'rank' fields.
        """
        if not coins:
            return []

        # --- Gather raw dimensions ---
        raw_trending = [self._safe_float(c.get("trending_score", 0)) for c in coins]

        # Price change: cap extreme outliers at +/-100%
        raw_price = [
            max(-100, min(100, self._safe_float(c.get("price_change_percentage_24h", 0))))
            for c in coins
        ]

        # Volume as a proportion of market cap (higher = more unusual activity)
        raw_volume = []
        for c in coins:
            vol = self._safe_float(c.get("total_volume", 0))
            mcap = self._safe_float(c.get("market_cap", 0))
            ratio = (vol / max(mcap, 1)) * 100 if mcap > 0 else 0.0
            raw_volume.append(min(ratio, 100))

        # --- Normalize each dimension to 0-100 ---
        norm_trending = self._normalize(raw_trending)
        norm_price = self._normalize([abs(v) for v in raw_price])  # Use absolute change as signal
        norm_volume = self._normalize(raw_volume)

        # --- Compute composite score ---
        scored = []
        for i, c in enumerate(coins):
            s = (
                norm_trending[i] * self.WEIGHTS["trending_score"]
                + norm_price[i] * self.WEIGHTS["price_change_24h"]
                + norm_volume[i] * self.WEIGHTS["volume_score"]
            )
            scored.append({
                **c,
                "signal_score": round(s, 1),
                "score_trending": round(norm_trending[i], 1),
                "score_price": round(norm_price[i], 1),
                "score_volume": round(norm_volume[i], 1),
            })

        # Sort by signal_score descending, then assign rank
        scored.sort(key=lambda x: x["signal_score"], reverse=True)
        for i, c in enumerate(scored):
            c["rank"] = i + 1

        return scored
