"""비트코인 묶음(lot) 단타 봇.

규칙
- 매달 MONTHLY_DAY일(기본 10일)에 MONTHLY_KRW(기본 10만 원)로 새 묶음을 산다.
- 묶음마다 자기 매수가 x (1 + TARGET) 이상이 되면 그 묶음만 팔고, 받은 돈 전부로 바로 다시 산다.
- 가격이 떨어지면 아무것도 안 하고 기다린다 (손절 없음).

사용법
  python bot.py run                  # 계속 실행
  python bot.py status               # 묶음 현황
  python bot.py add-lot --price 145000000 --volume 0.00068   # 이미 가진 코인 등록
  python bot.py remove-lot --id 3
"""
import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).resolve().parent


def load_env(path=HERE / ".env"):
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Config:
    market: str = "KRW-BTC"
    target: float = 0.005          # +0.5%
    fee: float = 0.0005            # 업비트 원화마켓 0.05%
    monthly_day: int = 10
    monthly_krw: int = 100_000
    min_order: int = 5_000
    poll_sec: int = 30
    dry_run: bool = True

    @classmethod
    def from_env(cls):
        e = os.environ.get
        return cls(
            market=e("MARKET", "KRW-BTC"),
            target=float(e("TARGET", "0.005")),
            fee=float(e("FEE", "0.0005")),
            monthly_day=int(e("MONTHLY_DAY", "10")),
            monthly_krw=int(e("MONTHLY_KRW", "100000")),
            poll_sec=int(e("POLL_SEC", "30")),
            dry_run=e("DRY_RUN", "1") not in ("0", "false", "False", "no"),
        )


@dataclass
class Lot:
    id: int
    price: float      # 평균 체결가 (이 가격 기준 +TARGET에 판다)
    volume: float
    cost: float       # 수수료 포함 들어간 원화
    opened: str


@dataclass
class State:
    lots: list = field(default_factory=list)
    next_id: int = 1
    last_monthly: str = ""        # 'YYYY-MM'
    cycles: int = 0
    realized: float = 0.0         # 실현 수익 합계 (원)
    pending_krw: float = 0.0      # 팔았는데 아직 다시 못 산 돈 (다음 틱에 재시도)
    paper_krw: float = 0.0        # 모의투자용 원화 잔고
    paper_deposit: str = ""       # 모의투자: 마지막으로 입금 처리한 달

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls()
        d = json.loads(path.read_text(encoding="utf-8"))
        d["lots"] = [Lot(**l) for l in d.get("lots", [])]
        return cls(**d)

    def save(self, path):
        tmp = path.with_suffix(".tmp")
        d = asdict(self)
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


# ---------------- 거래소 (실전 / 모의) ----------------
class LiveExchange:
    def __init__(self, client, cfg):
        self.c, self.cfg = client, cfg

    def price(self):
        return self.c.price(self.cfg.market)

    def krw(self):
        return self.c.balances().get("KRW", 0.0)

    def coin(self):
        return self.c.balances().get(self.cfg.market.split("-")[1], 0.0)

    def buy(self, krw):
        o = self.c.buy_market(self.cfg.market, krw)
        vol, avg, funds, fee = self.c.wait_fill(o["uuid"])
        return vol, avg, funds + fee           # 수량, 평균가, 실제 나간 원화

    def sell(self, volume):
        o = self.c.sell_market(self.cfg.market, volume)
        vol, avg, funds, fee = self.c.wait_fill(o["uuid"])
        return vol, avg, funds - fee           # 수량, 평균가, 실제 들어온 원화


class PaperExchange:
    """실제 주문 없이 현재가로 체결됐다고 치는 모의 거래소."""
    def __init__(self, price_fn, cfg, state):
        self.price_fn, self.cfg, self.st = price_fn, cfg, state

    def price(self):
        return self.price_fn()

    def krw(self):
        return self.st.paper_krw

    def coin(self):
        return sum(l.volume for l in self.st.lots)

    def buy(self, krw):
        p = self.price()
        fee = krw * self.cfg.fee
        self.st.paper_krw -= krw + fee
        return krw / p, p, krw + fee

    def sell(self, volume):
        p = self.price()
        gross = volume * p
        net = gross - gross * self.cfg.fee
        self.st.paper_krw += net
        return volume, p, net


# ---------------- 전략 ----------------
class Bot:
    def __init__(self, cfg, ex, state, state_path, log, notify=lambda m: None):
        self.cfg, self.ex, self.st, self.path = cfg, ex, state, state_path
        self.log, self.notify = log, notify
        self._warned_day = ""

    def _say(self, msg):
        self.log.info(msg)
        self.notify(msg)

    def _buy_lot(self, budget, now, why):
        """수수료 포함 budget 원 안에서 매수해 새 묶음을 만든다."""
        krw = int(budget / (1 + self.cfg.fee))
        if krw < self.cfg.min_order:
            self.log.warning(f"매수 금액 {krw:,}원이 최소 주문금액보다 작아 건너뜀")
            return None
        vol, avg, spent = self.ex.buy(krw)
        lot = Lot(self.st.next_id, avg, vol, spent, now.isoformat(timespec="seconds"))
        self.st.next_id += 1
        self.st.lots.append(lot)
        self.st.save(self.path)
        self._say(f"[매수·{why}] #{lot.id} {avg:,.0f}원 x {vol:.8f} = {spent:,.0f}원 "
                  f"→ 목표 {avg * (1 + self.cfg.target):,.0f}원")
        return lot

    def tick(self, now=None):
        now = now or datetime.now(KST)
        price = self.ex.price()

        # 1) 목표가 도달한 묶음 매도 → 받은 돈은 '재매수 대기'로
        for lot in sorted(self.st.lots, key=lambda l: l.price):
            if price < lot.price * (1 + self.cfg.target):
                continue
            vol, avg, got = self.ex.sell(lot.volume)
            self.st.lots.remove(lot)
            profit = got - lot.cost
            self.st.cycles += 1
            self.st.realized += profit
            self.st.pending_krw += got
            self.st.save(self.path)
            self._say(f"[매도] #{lot.id} {lot.price:,.0f} → {avg:,.0f}원, 수익 {profit:+,.0f}원 "
                      f"(누적 {self.st.realized:+,.0f}원, {self.st.cycles}회)")

        # 2) 재매수 (실패해도 다음 틱에 다시 시도)
        if self.st.pending_krw >= self.cfg.min_order:
            budget = min(self.st.pending_krw, self.ex.krw())
            if self._buy_lot(budget, now, "재매수"):
                self.st.pending_krw = 0.0
                self.st.save(self.path)

        # 3) 매달 정기 매수
        ym = now.strftime("%Y-%m")
        if now.day >= self.cfg.monthly_day and self.st.last_monthly != ym:
            if self.cfg.dry_run and self.st.paper_deposit != ym:
                self.st.paper_deposit = ym
                self.st.paper_krw += self.cfg.monthly_krw   # 모의: 입금했다고 침
            bal = self.ex.krw() - self.st.pending_krw
            # 1% 정도 모자라도 그 돈만큼 산다 (수수료 때문에 99,950원만 남는 경우 등)
            if bal >= self.cfg.monthly_krw * 0.99:
                self._buy_lot(min(self.cfg.monthly_krw, bal), now, f"{ym} 정기")
                self.st.last_monthly = ym
                self.st.save(self.path)
            elif self._warned_day != now.strftime("%Y-%m-%d"):
                self._warned_day = now.strftime("%Y-%m-%d")
                self._say(f"[대기] {ym} 정기 매수 대기 중: 원화 잔고 {bal:,.0f}원 "
                          f"(필요 {self.cfg.monthly_krw:,}원). 입금되면 자동으로 삽니다.")

    def status(self):
        price = self.ex.price()
        lines = [f"현재가 {price:,.0f}원 | 묶음 {len(self.st.lots)}개 | "
                 f"완료 {self.st.cycles}회 | 실현수익 {self.st.realized:+,.0f}원"]
        total_cost = total_val = 0
        for l in sorted(self.st.lots, key=lambda l: l.price):
            val = l.volume * price * (1 - self.cfg.fee)
            total_cost += l.cost
            total_val += val
            tgt = l.price * (1 + self.cfg.target)
            lines.append(f"  #{l.id:<3} 매수 {l.price:>13,.0f}  목표 {tgt:>13,.0f} "
                         f"({(tgt / price - 1) * 100:+6.2f}% 남음)  평가 {val - l.cost:+9,.0f}원  {l.opened[:10]}")
        if self.st.lots:
            lines.append(f"  보유 평가손익 {total_val - total_cost:+,.0f}원 (원금 {total_cost:,.0f}원)")
        return "\n".join(lines)


def telegram_notifier():
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return lambda m: None
    import requests

    def send(msg):
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": msg}, timeout=10)
        except Exception:
            pass
    return send


def main(argv=None):
    load_env()
    cfg = Config.from_env()
    ap = argparse.ArgumentParser(description="업비트 비트코인 묶음 단타 봇")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("status")
    a = sub.add_parser("add-lot", help="이미 가진 코인을 묶음으로 등록")
    a.add_argument("--price", type=float, required=True, help="매수 평균가")
    a.add_argument("--volume", type=float, required=True, help="수량")
    a.add_argument("--cost", type=float, help="들어간 원화 (생략하면 가격x수량x(1+수수료))")
    r = sub.add_parser("remove-lot")
    r.add_argument("--id", type=int, required=True)
    args = ap.parse_args(argv)

    state_path = HERE / ("state_paper.json" if cfg.dry_run else "state.json")
    st = State.load(state_path)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(HERE / "bot.log", encoding="utf-8")])
    log = logging.getLogger("bot")

    from upbit_client import UpbitClient
    client = UpbitClient(os.environ.get("UPBIT_ACCESS_KEY"), os.environ.get("UPBIT_SECRET_KEY"))
    if cfg.dry_run:
        ex = PaperExchange(lambda: client.price(cfg.market), cfg, st)
    else:
        ex = LiveExchange(client, cfg)
    bot = Bot(cfg, ex, st, state_path, log, telegram_notifier())

    if args.cmd == "add-lot":
        cost = args.cost or args.price * args.volume * (1 + cfg.fee)
        lot = Lot(st.next_id, args.price, args.volume, cost,
                  datetime.now(KST).isoformat(timespec="seconds"))
        st.next_id += 1
        st.lots.append(lot)
        st.save(state_path)
        print(f"#{lot.id} 등록: {args.price:,.0f}원 x {args.volume:.8f}")
        return
    if args.cmd == "remove-lot":
        st.lots = [l for l in st.lots if l.id != args.id]
        st.save(state_path)
        print(f"#{args.id} 삭제")
        return
    if args.cmd == "status":
        print(bot.status())
        return

    mode = "모의투자(DRY_RUN)" if cfg.dry_run else "실전"
    bot._say(f"봇 시작 [{mode}] {cfg.market} 목표 +{cfg.target * 100:.2f}% "
             f"매달 {cfg.monthly_day}일 {cfg.monthly_krw:,}원, 묶음 {len(st.lots)}개")
    if not cfg.dry_run:
        held = ex.coin()
        mine = sum(l.volume for l in st.lots)
        if mine > held * 1.0001:
            log.error(f"장부의 코인({mine:.8f})이 실제 잔고({held:.8f})보다 많습니다. 확인 후 다시 실행하세요.")
            return
    fails = 0
    while True:
        try:
            bot.tick()
            fails = 0
        except Exception as e:
            fails += 1
            log.exception(f"오류 ({fails}회 연속): {e}")
            if fails in (3, 30):
                bot.notify(f"[오류] {fails}회 연속 실패: {e}")
            time.sleep(min(300, 10 * fails))
        time.sleep(cfg.poll_sec)


if __name__ == "__main__":
    main()
