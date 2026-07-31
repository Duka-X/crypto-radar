import math as m
class SignalScorer:
    WEIGHTS = {"trending_score":0.17,"price_change_24h":0.13,"momentum":0.17,"volume_score":0.35}

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

    def _pa(self, coin):
        sp = coin.get("sparkline_prices", []) or []
        if len(sp) < 4:
            # Fallback: use 24h price change from Binance
            return (coin.get("price_change_percentage_24h", 0) or 0) / 100.0
        pn = sp[-1]; p1 = sp[-2]; p24 = sp[0]
        c1 = (pn - p1) / max(p1, 1e-10)
        c24 = (pn - p24) / max(p24, 1e-10)
        return (c1 * 24) - c24

    def score(self, coins):
        if not coins: return []
        rt = [self._f(c.get("trending_score",0)) for c in coins]
        rp = [self._pa(c) for c in coins]
        rtv = [self._f(c.get("trending_velocity",0)) for c in coins]
        rm = [self._momentum(c.get("sparkline_full",[]) or c.get("sparkline_prices",[])) for c in coins]
        # Pure volume growth signal (ignoring absolute volume)
        rv = [max(self._f(c.get("volume_growth", 1.0)) - 1.0, 0) for c in coins]
        # Log-scale so a single extreme growth coin doesn't crush the rest
        rv = [m.log(1 + x) for x in rv]
        nt = self._n(rt); ntv = self._n(rtv); np_ = self._n(rp)
        nm = self._n(rm); nv = self._n(rv)
        scored = []
        for i, c in enumerate(coins):
            t_i = nt[i]*0.4 + ntv[i]*0.6
            s = t_i*0.222 + np_[i]*0.278 + nm[i]*0.278 + nv[i]*0.222
            scored.append({**c, "signal_score": round(s,1),
                "score_trending": round(t_i,1), "score_price": round(np_[i],1),
                "score_momentum": round(nm[i],1), "score_volume": round(nv[i],1),
                })
        scored.sort(key=lambda x: x["signal_score"], reverse=True)
        for i, c in enumerate(scored): c["rank"] = i+1
        return scored
