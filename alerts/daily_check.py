"""매일 오전 알림용: 비트코인 50일선 신호 + ISA(나스닥100 70 / 금 30) 적립 안내와 하락 경고. 공개 시세만 사용."""
import json, urllib.request, time
from datetime import datetime, timedelta, timezone
KST = timezone(timedelta(hours=9)); now = datetime.now(KST); today = now.date().isoformat()
def get(url):
    for k in range(2):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20))
        except Exception:
            if k: raise
            time.sleep(60)
out = {"KST": today, "day": now.day, "month": now.month}
try:  # 비트코인 50일선 (업비트 일봉은 09시 KST에 바뀜)
    c = get("https://api.upbit.com/v1/candles/days?market=KRW-BTC&count=60")
    tday = (now - timedelta(hours=9)).date().isoformat()
    cl = [p for _, p in sorted((x["candle_date_time_kst"][:10], float(x["trade_price"])) for x in c if x["candle_date_time_kst"][:10] < tday)]
    m1, m0 = sum(cl[-50:]) / 50, sum(cl[-51:-1]) / 50
    out["btc"] = {"종가": round(cl[-1]), "50일선": round(m1), "차이%": round((cl[-1] / m1 - 1) * 100, 1),
                  "오늘신호": "보유" if cl[-1] > m1 else "현금", "어제신호": "보유" if cl[-2] > m0 else "현금"}
except Exception as e: out["btc"] = {"error": str(e)}
try:  # ISA: KODEX 미국나스닥100(379810) 70 / ACE KRX금현물(411060) 30, 전일 종가 기준
    def series(code):
        r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.KS?range=2y&interval=1d")["chart"]["result"][0]
        s = [(datetime.fromtimestamp(t, KST).date().isoformat(), x) for t, x in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if x]
        return [x for x in s if x[0] < today]
    nq, gd = series("379810"), series("411060")
    y0 = f"{now.year}-01-01"
    p0 = lambda s: next((p for d, p in s if d >= y0), s[-1][1])
    a, b = 0.7 * nq[-1][1] / p0(nq), 0.3 * gd[-1][1] / p0(gd)
    hi, hi_prev = max(p for _, p in nq[-250:]), max(p for _, p in nq[-251:-1])
    dd, dd_prev = nq[-1][1] / hi - 1, nq[-2][1] / hi_prev - 1
    pn, pg = nq[-1][1], gd[-1][1]
    best = min(((i, j) for i in range(0, 5) for j in range(0, 5) if 0 < i * pn + j * pg <= 100000),
               key=lambda t: (abs(t[0] * pn / (t[0] * pn + t[1] * pg) - 0.7) + (100000 - t[0] * pn - t[1] * pg) / 100000 * 0.5))
    out["isa"] = {"기준일": nq[-1][0], "나스닥100_1주": pn, "금_1주": pg,
                  "올해_나스닥%": round((pn / p0(nq) - 1) * 100, 1), "올해_금%": round((pg / p0(gd) - 1) * 100, 1),
                  "가격변동만_반영한_나스닥비중%": round(a / (a + b) * 100, 1),
                  "기본_매수": f"나스닥100 {best[0]}주 + 금 {best[1]}주 = {best[0] * pn + best[1] * pg:,.0f}원",
                  "나스닥_1년고점대비%": round(dd * 100, 1),
                  "하락구간_새로진입": next((t for t in (-30, -20) if dd * 100 <= t < dd_prev * 100), None),
                  "하락구간_회복": dd_prev * 100 <= -20 < dd * 100}
except Exception as e: out["isa"] = {"error": str(e)}
print(json.dumps(out, ensure_ascii=False, indent=1))
