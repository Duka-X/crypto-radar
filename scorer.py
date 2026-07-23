import math
class SignalScorer:
    WEIGHTS = {"trending_score":0.20,"price_change_24h":0.15,"momentum_score":0.20,"volume_score":0.25,"community_score":0.20}
    def _n(self,v):
        if not v:
            return []
        mn = min(v)
        mx = max(v)
        if mx == mn:
            return [0.0] * len(v)
        return [(x - mn) / (mx - mn) * 100 for x in v]
    def _f(self,x):
        try: return float(x) if x is not None else 0.0
        except: return 0.0
    def score(self,coins):
        if not coins: return []
        rt=[self._f(c.get("trending_score",0)) for c in coins]
        rp=[self._f(c.get("price_change_percentage_24h",0)) for c in coins]
        rm=[self._f(c.get("momentum_score",0)) for c in coins]
        rv=[min(self._f(c.get("total_volume",0))/max(self._f(c.get("market_cap",0)),1)*100,100) if self._f(c.get("market_cap",0))>0 else 0 for c in coins]
        rc_dev=[self._f(c.get("community_score",0)) for c in coins]
        rc_reddit=[self._f(c.get("community_raw",0)) for c in coins]
        # community_raw is already log-scaled
        rc = [d + r for d, r in zip(rc_dev, rc_reddit)]
        nt=self._n(rt);np_=self._n([abs(x) for x in rp]);nm=self._n(rm);nv=self._n(rv);nc=self._n(rc)
        "# Soften community extremes: map [0,100] to [5,95] so no coin gets absolute 0"
        nc = [5 + x * 0.9 for x in nc]
        scored=[]
        for i,c in enumerate(coins):
            s=nt[i]*0.20+np_[i]*0.15+nm[i]*0.20+nv[i]*0.25+nc[i]*0.20
            scored.append({**c,"signal_score":round(s,1),"score_trending":round(nt[i],1),"score_price":round(np_[i],1),"score_momentum":round(nm[i],1),"score_volume":round(nv[i],1),"score_community":round(nc[i],1)})
        scored.sort(key=lambda x:x["signal_score"],reverse=True)
        for i,c in enumerate(scored): c["rank"]=i+1
        return scored