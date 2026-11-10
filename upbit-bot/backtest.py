"""50일선 추세추종 + 매달 적립 vs 그냥 매달 사서 보유 백테스트 (업비트 일봉).

  python backtest.py                          # 최근 3년
  python backtest.py --years 6                # 최근 6년
  python backtest.py --start 2023-11-10 --end 2025-10-09
  python backtest.py --ma 20 --ma 50 --ma 120 # 여러 이평선 비교

봇과 같은 규칙: 어제 종가와 50일선으로 판단해서 오늘 시가에 사고판다 (미래 정보 안 씀).
매달 MONTHLY_DAY일(또는 그 뒤 첫 거래일)에 MONTHLY원 입금.
"""
import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fetch_days(market, n_days):
    from upbit_client import UpbitClient
    c, rows, to = UpbitClient(), {}, None
    while len(rows) < n_days:
        b = c.day_candles(market, to=to, count=200)
        if not b:
            break
        for x in b:
            rows[x["candle_date_time_kst"][:10]] = (x["opening_price"], x["high_price"],
                                                    x["low_price"], x["trade_price"])
        to = b[-1]["candle_date_time_utc"].replace("T", " ")
        time.sleep(0.15)
    return sorted(rows.items())


def simulate(days, start, end, ma=None, monthly=100_000, mday=10, fee=0.0005):
    """ma=None 이면 매달 사서 보유. 반환: (평가금액, 넣은 돈, 거래수, 넣은 돈 대비 최악, 끝 상태)"""
    dates = [d for d, _ in days]
    O = [v[0] for _, v in days]
    C = [v[3] for _, v in days]
    i0 = next(i for i, d in enumerate(dates) if d >= start)
    i1 = max(i for i, d in enumerate(dates) if d <= end)
    if ma and i0 < ma:
        raise SystemExit(f"{ma}일선을 계산하려면 시작일 전 데이터가 더 필요합니다")
    cash = btc = put = 0.0
    trades, worst, done = 0, 0.0, set()
    for i in range(i0, i1 + 1):
        ym = dates[i][:7]
        if int(dates[i][8:]) >= mday and ym not in done:
            done.add(ym)
            cash += monthly
            put += monthly
            if ma is None:
                btc += cash / (1 + fee) / O[i]
                cash = 0
                trades += 1
        if ma:
            avg = sum(C[i - ma:i]) / ma          # 어제까지 ma일 평균
            if C[i - 1] > avg and cash >= 5000:
                btc += cash / (1 + fee) / O[i]
                cash = 0
                trades += 1
            elif C[i - 1] <= avg and btc * O[i] >= 5000:
                cash += btc * O[i] * (1 - fee)
                btc = 0
                trades += 1
        val = cash + btc * C[i] * (1 - fee)
        worst = min(worst, val / put - 1)
    val = cash + btc * C[i1] * (1 - fee)
    return val, put, trades, worst, ("보유" if btc else "현금"), dates[i0], dates[i1], O[i0], C[i1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KRW-BTC")
    ap.add_argument("--years", type=float, default=3)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--ma", type=int, action="append", help="이평선 일수 (여러 번 가능, 기본 50)")
    ap.add_argument("--monthly", type=int, default=100_000)
    ap.add_argument("--day", type=int, default=10)
    ap.add_argument("--fee", type=float, default=0.0005)
    a = ap.parse_args()
    mas = a.ma or [50]

    end = a.end or date.today().isoformat()
    start = a.start or (date.fromisoformat(end) - timedelta(days=int(365 * a.years))).isoformat()
    need = (date.today() - date.fromisoformat(start)).days + max(mas) + 10
    cache = HERE / f"days_{a.market}.json"
    days = None
    if cache.exists():
        days = json.loads(cache.read_text())
        if days[0][0] > (date.fromisoformat(start) - timedelta(days=max(mas) + 5)).isoformat() \
                or days[-1][0] < (date.today() - timedelta(days=1)).isoformat():
            days = None
    if days is None:
        print("업비트에서 일봉 받는 중...")
        days = fetch_days(a.market, need)
        cache.write_text(json.dumps(days))

    base = simulate(days, start, end, None, a.monthly, a.day, a.fee)
    print(f"\n기간 {base[5]} ~ {base[6]} | 비트코인 {base[7]:,.0f} → {base[8]:,.0f}원 "
          f"({(base[8] / base[7] - 1) * 100:+.0f}%) | 넣은 돈 {base[1]:,.0f}원\n")
    rows = [("매달 사서 보유", base)] + [(f"{m}일선 추세추종", simulate(days, start, end, m, a.monthly, a.day, a.fee))
                                    for m in mas]
    for name, r in rows:
        print(f"{name:14} {r[0]:>13,.0f}원 ({(r[0] / r[1] - 1) * 100:+6.1f}%)  거래 {r[2]:3}회  "
              f"넣은 돈 대비 최악 {r[3] * 100:+6.1f}%  지금 {r[4]}")


if __name__ == "__main__":
    main()
