"""비트코인 50일선 추세추종 + 매달 적립 봇.

규칙 (하루 한 번, 업비트 일봉이 바뀌는 오전 9시 KST 이후에 판단)
- 어제 종가 > 최근 50일 종가 평균(50일선)  → '보유': 원화가 있으면 전부 비트코인으로 산다.
- 어제 종가 <= 50일선                      → '현금': 비트코인을 전부 판다.
- 매달 10일 전후로 입금한 원화는 '보유' 상태면 바로 사고, '현금' 상태면 신호가 바뀔 때까지 기다린다.
- 봇은 자기가 산 비트코인(bot_btc)만 판다. 원래 갖고 있던 비트코인은 건드리지 않는다.
- 원화는 KRW_RESERVE 를 뺀 전부를 쓴다.

사용법
  python bot.py run       # 계속 실행 (POLL_SEC 마다 확인)
  python bot.py once      # 한 번만 확인하고 끝 (cron 으로 하루 몇 번 돌릴 때)
  python bot.py status    # 현재 신호와 잔고
  python bot.py check     # API 키가 잘 되는지 확인 (주문 없음)
  python bot.py test-order  # 실전 키로 6,000원 사고 바로 팔아보기 (수수료 약 6원)
"""
import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
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
    ma_days: int = 50
    fee: float = 0.0005
    krw_reserve: int = 0           # 봇이 건드리지 않고 남겨둘 원화
    monthly_day: int = 10          # 모의투자에서 입금했다고 칠 날
    monthly_krw: int = 100_000
    min_order: int = 5_000
    poll_sec: int = 300
    dry_run: bool = True

    @classmethod
    def from_env(cls):
        e = os.environ.get
        return cls(
            market=e("MARKET", "KRW-BTC"),
            ma_days=int(e("MA_DAYS", "50")),
            fee=float(e("FEE", "0.0005")),
            krw_reserve=int(e("KRW_RESERVE", "0")),
            monthly_day=int(e("MONTHLY_DAY", "10")),
            monthly_krw=int(e("MONTHLY_KRW", "100000")),
            poll_sec=int(e("POLL_SEC", "300")),
            dry_run=e("DRY_RUN", "1") not in ("0", "false", "False", "no"),
        )


@dataclass
class State:
    signal_day: str = ""          # 신호를 계산한 거래일 (YYYY-MM-DD, 오전 9시 기준)
    signal: str = ""              # 'hold' | 'cash'
    close: float = 0.0
    ma: float = 0.0
    bot_btc: float = 0.0          # 봇이 산 비트코인 (봇은 이것만 판다)
    history: list = field(default_factory=list)   # 최근 거래 기록
    paper_krw: float = 0.0
    paper_btc: float = 0.0
    paper_deposit: str = ""
    paper_invested: float = 0.0

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls()
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def trading_day(now):
    """업비트 일봉은 오전 9시(KST)에 바뀐다."""
    return (now - timedelta(hours=9)).date().isoformat()


def compute_signal(candles, today, ma_days):
    """완성된 일봉(오늘 진행 중인 봉 제외)으로 (어제 종가, 50일선, 신호) 계산."""
    done = sorted((c["candle_date_time_kst"][:10], float(c["trade_price"])) for c in candles
                  if c["candle_date_time_kst"][:10] < today)
    if len(done) < ma_days:
        raise RuntimeError(f"일봉이 부족합니다 ({len(done)}개)")
    closes = [p for _, p in done[-ma_days:]]
    ma = sum(closes) / ma_days
    close = closes[-1]
    return close, ma, ("hold" if close > ma else "cash")


# ---------------- 거래소 (실전 / 모의) ----------------
class LiveExchange:
    def __init__(self, client, cfg):
        self.c, self.cfg = client, cfg
        self.coin_name = cfg.market.split("-")[1]

    def price(self):
        return self.c.price(self.cfg.market)

    def day_candles(self, n):
        return self.c.day_candles(self.cfg.market, count=n)

    def balances(self):
        b = self.c.balances()
        return b.get("KRW", 0.0), b.get(self.coin_name, 0.0)

    def buy(self, krw):
        o = self.c.buy_market(self.cfg.market, krw)
        vol, avg, funds, fee = self.c.wait_fill(o["uuid"])
        return vol, avg, funds + fee

    def sell(self, volume):
        o = self.c.sell_market(self.cfg.market, volume)
        vol, avg, funds, fee = self.c.wait_fill(o["uuid"])
        return vol, avg, funds - fee


class PaperExchange:
    """실제 주문 없이 현재가로 체결됐다고 치는 모의 거래소."""
    def __init__(self, client, cfg, state):
        self.c, self.cfg, self.st = client, cfg, state

    def price(self):
        return self.c.price(self.cfg.market)

    def day_candles(self, n):
        return self.c.day_candles(self.cfg.market, count=n)

    def balances(self):
        return self.st.paper_krw, self.st.paper_btc

    def buy(self, krw):
        p = self.price()
        self.st.paper_krw -= krw * (1 + self.cfg.fee)
        self.st.paper_btc += krw / p
        return krw / p, p, krw * (1 + self.cfg.fee)

    def sell(self, volume):
        p = self.price()
        net = volume * p * (1 - self.cfg.fee)
        self.st.paper_btc -= volume
        self.st.paper_krw += net
        return volume, p, net


# ---------------- 전략 ----------------
class Bot:
    def __init__(self, cfg, ex, state, state_path, log, notify=lambda m: None):
        self.cfg, self.ex, self.st, self.path = cfg, ex, state, state_path
        self.log, self.notify = log, notify

    def _say(self, msg):
        self.log.info(msg)
        self.notify(msg)

    def _record(self, now, kind, vol, avg, krw):
        self.st.history.append({"time": now.isoformat(timespec="seconds"), "type": kind,
                                "volume": round(vol, 8), "price": avg, "krw": round(krw)})
        self.st.history = self.st.history[-200:]
        self.st.save(self.path)

    def update_signal(self, now):
        today = trading_day(now)
        if self.st.signal_day == today:
            return False
        close, ma, sig = compute_signal(self.ex.day_candles(self.cfg.ma_days + 5), today, self.cfg.ma_days)
        changed = sig != self.st.signal
        prev = self.st.signal
        self.st.signal_day, self.st.signal, self.st.close, self.st.ma = today, sig, close, ma
        self.st.save(self.path)
        word = "보유" if sig == "hold" else "현금"
        msg = (f"[{today}] 어제 종가 {close:,.0f}원 / {self.cfg.ma_days}일선 {ma:,.0f}원 "
               f"({(close / ma - 1) * 100:+.1f}%) → {word}")
        if changed and prev:
            self._say(f"[신호 변경] {msg}")
        else:
            self.log.info(msg)
        return changed

    def tick(self, now=None):
        now = now or datetime.now(KST)

        # 모의투자: 매달 입금했다고 침
        if self.cfg.dry_run:
            ym = now.strftime("%Y-%m")
            if now.day >= self.cfg.monthly_day and self.st.paper_deposit != ym:
                self.st.paper_deposit = ym
                self.st.paper_krw += self.cfg.monthly_krw
                self.st.paper_invested += self.cfg.monthly_krw
                self.st.save(self.path)
                self.log.info(f"[모의 입금] {self.cfg.monthly_krw:,}원")

        self.update_signal(now)
        krw, btc = self.ex.balances()

        if self.st.signal == "hold":
            spend = int((krw - self.cfg.krw_reserve) / (1 + self.cfg.fee))
            if spend >= self.cfg.min_order:
                vol, avg, cost = self.ex.buy(spend)
                self.st.bot_btc += vol
                self._record(now, "buy", vol, avg, cost)
                self._say(f"[매수] {avg:,.0f}원 x {vol:.8f} = {cost:,.0f}원 (봇 보유 {self.st.bot_btc:.8f})")
        elif self.st.signal == "cash":
            sell_vol = min(self.st.bot_btc, btc)     # 봇이 산 만큼만 판다
            if sell_vol < self.st.bot_btc * 0.999:
                self.log.warning(f"봇 장부({self.st.bot_btc:.8f})보다 실제 잔고({btc:.8f})가 적어 잔고만큼만 팝니다")
            if sell_vol * self.ex.price() >= self.cfg.min_order:
                vol, avg, got = self.ex.sell(sell_vol)
                self.st.bot_btc = max(0.0, self.st.bot_btc - vol)
                self._record(now, "sell", vol, avg, got)
                self._say(f"[매도] {avg:,.0f}원 x {vol:.8f} = {got:,.0f}원 (현금으로 대기)")

    def status(self):
        now = datetime.now(KST)
        self.update_signal(now)
        price = self.ex.price()
        krw, btc = self.ex.balances()
        word = "보유 (50일선 위)" if self.st.signal == "hold" else "현금 (50일선 아래)"
        lines = [
            f"신호: {word}  [{self.st.signal_day}]",
            f"  어제 종가 {self.st.close:,.0f}원 / {self.cfg.ma_days}일선 {self.st.ma:,.0f}원 "
            f"({(self.st.close / self.st.ma - 1) * 100:+.1f}%)",
            f"  현재가 {price:,.0f}원",
            f"원화 {krw:,.0f}원 (봇이 쓰는 돈: {max(0, krw - self.cfg.krw_reserve):,.0f}원)",
            f"비트코인 봇 보유분 {self.st.bot_btc:.8f} (≈ {self.st.bot_btc * price:,.0f}원)  ← 신호 따라 사고팜",
            f"비트코인 기존 보유분 {max(0.0, btc - self.st.bot_btc):.8f} "
            f"(≈ {max(0.0, btc - self.st.bot_btc) * price:,.0f}원)  ← 봇이 건드리지 않음",
        ]
        if self.cfg.dry_run:
            tot = krw + self.st.bot_btc * price * (1 - self.cfg.fee)
            if self.st.paper_invested:
                lines.append(f"  모의투자 넣은 돈 {self.st.paper_invested:,.0f}원 → "
                             f"{(tot / self.st.paper_invested - 1) * 100:+.1f}%")
        for h in self.st.history[-5:]:
            lines.append(f"  {h['time'][:16]} {'매수' if h['type'] == 'buy' else '매도'} "
                         f"{h['price']:,.0f}원 {h['krw']:,}원")
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
    ap = argparse.ArgumentParser(description="업비트 비트코인 50일선 추세추종 봇")
    ap.add_argument("cmd", choices=["run", "once", "status", "check", "test-order"])
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
    ex = PaperExchange(client, cfg, st) if cfg.dry_run else LiveExchange(client, cfg)
    bot = Bot(cfg, ex, st, state_path, log, telegram_notifier())

    if args.cmd == "status":
        print(bot.status())
        return
    if args.cmd == "check":
        try:
            b = client.balances()
        except Exception as e:
            print(f"실패: {e}\n→ .env 의 키, 업비트에 등록한 IP, '자산조회' 권한을 확인하세요.")
            return
        print("API 키 정상. 잔고:", {k: v for k, v in b.items() if v})
        print("모드:", "모의투자(DRY_RUN=1)" if cfg.dry_run else "실전(DRY_RUN=0)")
        return
    if args.cmd == "test-order":
        live = LiveExchange(client, cfg)
        krw, _ = live.balances()
        if krw < 6100:
            print(f"원화가 6,100원 이상 있어야 합니다 (지금 {krw:,.0f}원)")
            return
        if input("실제로 6,000원어치 사고 바로 팝니다. 진행할까요? (yes 입력): ").strip() != "yes":
            return
        vol, avg, cost = live.buy(6000)
        print(f"매수 체결: {avg:,.0f}원 x {vol:.8f} = {cost:,.0f}원")
        vol2, avg2, got = live.sell(vol)
        print(f"매도 체결: {avg2:,.0f}원 x {vol2:.8f} = {got:,.0f}원  → 비용 {cost - got:,.0f}원")
        print("실전 주문 정상. (봇 장부에는 기록하지 않음)")
        return
    if args.cmd == "once":
        bot.tick()
        return

    mode = "모의투자(DRY_RUN)" if cfg.dry_run else "실전"
    bot._say(f"봇 시작 [{mode}] {cfg.market} {cfg.ma_days}일선 추세추종")
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
