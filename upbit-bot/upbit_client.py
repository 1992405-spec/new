"""업비트 Open API 최소 클라이언트 (시세 조회, 잔고, 시장가 주문, 주문 체결 확인)."""
import hashlib
import time
import uuid
from urllib.parse import urlencode

import jwt
import requests

BASE = "https://api.upbit.com"


class UpbitError(Exception):
    pass


class UpbitClient:
    def __init__(self, access_key=None, secret_key=None, timeout=10):
        self.ak = access_key
        self.sk = secret_key
        self.timeout = timeout
        self.s = requests.Session()

    # ---- 인증 ----
    def _headers(self, params=None):
        if not (self.ak and self.sk):
            raise UpbitError("API 키가 없습니다 (UPBIT_ACCESS_KEY / UPBIT_SECRET_KEY)")
        payload = {"access_key": self.ak, "nonce": str(uuid.uuid4())}
        if params:
            q = urlencode(params).encode()
            payload["query_hash"] = hashlib.sha512(q).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        token = jwt.encode(payload, self.sk, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    def _check(self, r):
        if r.status_code >= 400:
            raise UpbitError(f"{r.status_code} {r.text}")
        return r.json()

    def _get(self, path, params=None, auth=False):
        h = self._headers(params) if auth else {}
        for attempt in range(3):
            try:
                r = self.s.get(BASE + path, params=params, headers=h, timeout=self.timeout)
                if r.status_code == 429:  # 요청 제한
                    time.sleep(1 + attempt)
                    if auth:
                        h = self._headers(params)
                    continue
                return self._check(r)
            except requests.RequestException as e:
                if attempt == 2:
                    raise UpbitError(str(e))
                time.sleep(1 + attempt)
        raise UpbitError(f"GET {path} 요청 제한 초과")

    def _post(self, path, params):
        # 주문은 중복 위험이 있어 재시도하지 않는다.
        r = self.s.post(BASE + path, json=params, headers=self._headers(params), timeout=self.timeout)
        return self._check(r)

    # ---- 공개 API ----
    def price(self, market):
        return float(self._get("/v1/ticker", {"markets": market})[0]["trade_price"])

    def candles(self, market, unit=60, to=None, count=200):
        params = {"market": market, "count": count}
        if to:
            params["to"] = to
        return self._get(f"/v1/candles/minutes/{unit}", params)

    # ---- 계정 ----
    def balances(self):
        return {a["currency"]: float(a["balance"]) for a in self._get("/v1/accounts", auth=True)}

    def buy_market(self, market, krw):
        """krw 원어치 시장가 매수 (수수료는 별도로 추가 차감됨)."""
        return self._post("/v1/orders", {"market": market, "side": "bid",
                                         "ord_type": "price", "price": str(int(krw))})

    def sell_market(self, market, volume):
        return self._post("/v1/orders", {"market": market, "side": "ask",
                                         "ord_type": "market", "volume": f"{volume:.8f}"})

    def wait_fill(self, order_uuid, timeout=30):
        """주문이 끝날 때까지 기다린 뒤 (체결수량, 평균체결가, 체결금액, 수수료) 반환."""
        deadline = time.time() + timeout
        while True:
            o = self._get("/v1/order", {"uuid": order_uuid}, auth=True)
            # 시장가 매수는 남은 잔돈이 취소되어 'cancel' 상태로 끝나는 게 정상
            if o["state"] in ("done", "cancel") and o.get("trades") is not None:
                vol = sum(float(t["volume"]) for t in o["trades"])
                funds = sum(float(t["funds"]) for t in o["trades"])
                if vol > 0:
                    return vol, funds / vol, funds, float(o.get("paid_fee", 0))
                if o["state"] == "cancel":
                    raise UpbitError(f"주문이 체결 없이 취소됨: {order_uuid}")
            if time.time() > deadline:
                raise UpbitError(f"주문 체결 확인 시간 초과: {order_uuid}")
            time.sleep(0.5)
