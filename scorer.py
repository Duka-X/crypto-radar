class SignalScorer:
    WEIGHTS = {"trending_score": 0.25, "google_trends": 0.20, "reddit_mentions": 0.20, "price_change_24h": 0.20, "volume_score": 0.15}
    def _normalize(self, v):
        if not v: return []
        mn, mx = min(v), max(v)
        if mx == mn: return [50.0]*len(v)
        return [(x-mn)/(mx-mn)*100 for x in v]
    def _sf(self, x):
        try: return float(x) if x is not None else 0.0
        except: return 0.0
    def score(self, coins):
        if not coins: return []
        rt = [self._sf(c.get("trending_score",0)) for c in coins]
        rg = [self._sf(c.get("google_trends_score",0)) for c in coins]
        rr = [self._sf(c.get("reddit_mentions",0)) for c in coins]
        rp = [max(-100, min(100, self._sf(c.get("price_change_percentage_24h",0)))) for c in coins]
        rv = [min((self._sf(c.get("total_volume",0))/max(self._sf(c.get("market_cap",0)),1))*100, 100) if self._sf(c.get("market_cap",0))>0 else 0 for c in coins]
        nt = self._normalize(rt); ng = self._normalize(rg); nr = self._normalize(rr)
        np_ = self._normalize([abs(x) for x in rp]); nv = self._normalize(rv)
        scored = []
        for i,c in enumerate(coins):
            s = nt[i]*0.25 + ng[i]*0.20 + nr[i]*0.20 + np_[i]*0.20 + nv[i]*0.15
            scored.append({**c, "signal_score": round(s,1), "score_trending": round(nt[i],1), "score_trends": round(ng[i],1), "score_reddit": round(nr[i],1), "score_price": round(np_[i],1), "score_volume": round(nv[i],1)})
        scored.sort(key=lambda x: x["signal_score"], reverse=True)
        for i,c in enumerate(scored): c["rank"] = i+1
        return scored
