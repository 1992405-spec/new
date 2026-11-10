"""오늘의 50일선 신호와 '어제 대비 바뀌었는지'를 출력한다. (API 키 불필요, 표준 라이브러리만 사용)

  python signal_check.py
"""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MA = 50

req = urllib.request.Request("https://api.upbit.com/v1/candles/days?market=KRW-BTC&count=60",
                             headers={"Accept": "application/json"})
candles = json.load(urllib.request.urlopen(req, timeout=15))
now = datetime.now(KST)
today = (now - timedelta(hours=9)).date().isoformat()          # 업비트 일봉은 오전 9시에 바뀜
done = sorted((c["candle_date_time_kst"][:10], float(c["trade_price"])) for c in candles
              if c["candle_date_time_kst"][:10] < today)
closes = [p for _, p in done]
ma_now = sum(closes[-MA:]) / MA
ma_prev = sum(closes[-MA - 1:-1]) / MA
sig_now = "보유" if closes[-1] > ma_now else "현금"
sig_prev = "보유" if closes[-2] > ma_prev else "현금"
print(f"기준일 {done[-1][0]} 종가 {closes[-1]:,.0f}원 / 50일선 {ma_now:,.0f}원 ({(closes[-1] / ma_now - 1) * 100:+.1f}%)")
print(f"신호: {sig_prev} → {sig_now}" + ("  ★ 바뀜" if sig_now != sig_prev else "  (변경 없음)"))
if sig_now != sig_prev:
    print("할 일:", "봇 몫 원화로 비트코인 매수" if sig_now == "보유" else "봇 몫 비트코인 전부 매도")
