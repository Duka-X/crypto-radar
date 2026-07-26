class SignalScorer:
    WEIGHTS = {"trending_score":0.20,"price_change_24h":0.15,"momentum":0.20,"volume_score":0.25,"community":0.20}

    def _n(self, v):
        if not v: return []
        mn, mx = min(v), max(v)
        if mx == mn: return [50.0]*len(v)
        return [(x-mn)/(mx-mn)*100 for x in v]

    def _f(self, x):
        try: return float(x) if x is not None else 0.0
        except: return 0.0

    def _momentum(self, sp):
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

    def _c_rank(self, c, coins):
        ranks = sorted([x.get("market_cap_rank", 999) or 999 for x in coins])
        r = c.get("market_cap_rank", 999) or 999
        idx = ranks.index(r)
        return round(95 - (idx / max(len(ranks)-1, 1)) * 90, 1)

    def score(self, coins):
        if not coins: return []
        rt = [self._f(c.get("trending_score",0)) for c in coins]
        rp = [max(-100, min(100, self._f(c.get("price_change_percentage_24h",0)))) for c in coins]
        rm = [self._momentum(c.get("sparkline_prices",[]) or c.get("sparkline_full",[])) for c in coins]
        rv = [min(self._f(c.get("total_volume",0))/max(self._f(c.get("market_cap",0)),1)*100,100) if self._f(c.get("market_cap",0))>0 else 0 for c in coins]
        rc = [self._c_rank(c, coins) for c in coins]
        nt = self._n(rt); np_ = self._n([abs(x) for x in rp])
        nm = self._n(rm); nv = self._n(rv)
        scored = []
        for i, c in enumerate(coins):
            s = nt[i]*0.20 + np_[i]*0.15 + nm[i]*0.20 + nv[i]*0.25 + rc[i]*0.20
            scored.append({**c, "signal_score": round(s,1),
                "score_trending": round(nt[i],1), "score_price": round(np_[i],1),
                "score_momentum": round(nm[i],1), "score_volume": round(nv[i],1),
                "score_community": round(rc[i],1)})
        scored.sort(key=lambda x: x["signal_score"], reverse=True)
        for i, c in enumerate(scored): c["rank"] = i+1
        return scored
