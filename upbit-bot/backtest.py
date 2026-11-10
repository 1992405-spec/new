"""과거 시세로 '묶음 +0.5% 단타' 전략을 돌려보고, 그냥 매달 사서 들고 있기(적립식)와 비교한다.

  python backtest.py --years 2                 # 업비트에서 1시간봉 받아서 2년 백테스트
  python backtest.py --years 3 --unit 10       # 10분봉 (더 정확, 더 오래 걸림)
  python backtest.py --csv candles.csv         # 저장된 캔들 파일로

캔들 안에서 고가가 목표가 이상이면 '목표가에 팔고 목표가에 다시 샀다'고 본다.
다시 산 묶음은 같은 캔들 안에서는 팔지 않는다(보수적으로).
"""
import argparse
import csv
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).resolve().parent


def fetch(market, unit, years):
    from upbit_client import UpbitClient
    c = UpbitClient()
    end = datetime.now(KST)
    start = end - timedelta(days=365 * years)
    rows, to = {}, None
    while True:
        batch = c.candles(market, unit, to=to, count=200)
        if not batch:
            break
        for b in batch:
            rows[b["candle_date_time_kst"]] = (b["opening_price"], b["high_price"],
                                               b["low_price"], b["trade_price"])
        oldest = batch[-1]["candle_date_time_utc"]
        to = oldest.replace("T", " ")
        print(f"\r캔들 {len(rows):,}개 받는 중... ({batch[-1]['candle_date_time_kst'][:10]})", end="", flush=True)
        if datetime.fromisoformat(batch[-1]["candle_date_time_kst"]).replace(tzinfo=KST) <= start:
            break
        time.sleep(0.12)
    print()
    out = [(datetime.fromisoformat(k).replace(tzinfo=KST), *v) for k, v in sorted(rows.items())]
    return [r for r in out if r[0] >= start]


def save_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_kst", "open", "high", "low", "close"])
        for r in rows:
            w.writerow([r[0].isoformat(), *r[1:]])


def load_csv(path):
    with open(path) as f:
        rd = csv.DictReader(f)
        rows = []
        for r in rd:
            t = datetime.fromisoformat(r["time_kst"])
            if t.tzinfo is None:
                t = t.replace(tzinfo=KST)
            rows.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    return rows


def simulate(rows, target=0.005, fee=0.0005, monthly=100_000, day=10):
    lots = []                      # [매수가, 수량, 원가, 산 시각]
    contributed = realized = 0.0
    cycles, max_lots, longest = 0, 0, timedelta(0)
    dca_vol = 0.0
    worst = (0.0, None)            # 가장 큰 평가손실률
    done_months = set()
    monthly_cycles = {}

    for t, o, h, l, c in rows:
        ym = t.strftime("%Y-%m")
        if t.day >= day and ym not in done_months:
            done_months.add(ym)
            contributed += monthly
            krw = monthly / (1 + fee)
            lots.append([o, krw / o, monthly, t])
            dca_vol += krw / o

        for lot in sorted(lots, key=lambda x: x[0]):   # 복사본이라 이번 캔들 재매수분은 안 돎
            tgt = lot[0] * (1 + target)
            if h < tgt:
                continue
            got = lot[1] * tgt * (1 - fee)
            realized += got - lot[2]
            cycles += 1
            monthly_cycles[ym] = monthly_cycles.get(ym, 0) + 1
            longest = max(longest, t - lot[3])
            lots.remove(lot)
            lots.append([tgt, got / (1 + fee) / tgt, got, t])

        max_lots = max(max_lots, len(lots))
        cost = sum(x[2] for x in lots)
        if cost:
            val = sum(x[1] for x in lots) * l * (1 - fee)
            dd = val / cost - 1
            if dd < worst[0]:
                worst = (dd, t)

    last_t, last = rows[-1][0], rows[-1][4]
    for lot in lots:
        longest = max(longest, last_t - lot[3])
    hold_val = sum(x[1] for x in lots) * last * (1 - fee)
    return {
        "기간": f"{rows[0][0]:%Y-%m-%d} ~ {last_t:%Y-%m-%d}",
        "시작가/마지막가": f"{rows[0][1]:,.0f} → {last:,.0f}원 ({(last / rows[0][1] - 1) * 100:+.1f}%)",
        "넣은 돈": contributed,
        "단타_평가금액": hold_val,
        "단타_수익": hold_val - contributed,
        "실현수익(이미 번 돈)": realized,
        "거래 횟수": cycles,
        "월평균 거래": cycles / max(1, len(done_months)),
        "최대 동시 묶음": max_lots,
        "가장 오래 묶인 기간": f"{longest.days}일",
        "최악 평가손실": f"{worst[0] * 100:.1f}% ({worst[1]:%Y-%m-%d})" if worst[1] else "없음",
        "남은 묶음": len(lots),
        "적립식_평가금액": dca_vol * last * (1 - fee),
        "적립식_수익": dca_vol * last * (1 - fee) - contributed,
    }


def report(r):
    print(f"\n=== 백테스트 {r['기간']} ===")
    print(f"비트코인 {r['시작가/마지막가']}")
    print(f"넣은 돈           {r['넣은 돈']:>14,.0f}원")
    print("-- 묶음 +0.5% 단타 --")
    print(f"평가금액          {r['단타_평가금액']:>14,.0f}원  (수익 {r['단타_수익']:+,.0f}원, "
          f"{r['단타_수익'] / r['넣은 돈'] * 100:+.1f}%)")
    print(f"실현수익          {r['실현수익(이미 번 돈)']:>14,.0f}원  ({r['거래 횟수']}회, 월평균 {r['월평균 거래']:.1f}회)")
    print(f"최대 동시 묶음 {r['최대 동시 묶음']}개 / 가장 오래 묶인 기간 {r['가장 오래 묶인 기간']} / "
          f"최악 평가손실 {r['최악 평가손실']} / 끝날 때 묶음 {r['남은 묶음']}개")
    print("-- 비교: 매달 사서 그냥 보유 --")
    print(f"평가금액          {r['적립식_평가금액']:>14,.0f}원  (수익 {r['적립식_수익']:+,.0f}원, "
          f"{r['적립식_수익'] / r['넣은 돈'] * 100:+.1f}%)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="KRW-BTC")
    ap.add_argument("--years", type=float, default=2)
    ap.add_argument("--unit", type=int, default=60, help="분봉 단위 (1,3,5,10,15,30,60,240)")
    ap.add_argument("--csv", help="캔들 CSV (없으면 업비트에서 받아 저장)")
    ap.add_argument("--target", type=float, default=0.005)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--monthly", type=int, default=100_000)
    ap.add_argument("--day", type=int, default=10)
    a = ap.parse_args()

    if a.csv and Path(a.csv).exists():
        rows = load_csv(a.csv)
    else:
        rows = fetch(a.market, a.unit, a.years)
        path = a.csv or HERE / f"candles_{a.market}_{a.unit}m_{a.years:g}y.csv"
        save_csv(rows, path)
        print(f"캔들 저장: {path}")
    report(simulate(rows, a.target, a.fee, a.monthly, a.day))


if __name__ == "__main__":
    main()
