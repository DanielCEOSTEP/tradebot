#!/usr/bin/env python3
"""
Paradex proto-bot:
 • берёт свежий JWT (jwt_util.get_jwt)
 • WS-авторизация  → подписка на order_book + account
 • вывод free_collateral и простейший спред-сканер
"""

import json, time, decimal, os, websocket
from jwt_util import get_jwt

# ─────────────────────── конфигурация ───────────────────────
ENV            = os.getenv("PARADEX_ENV", "prod")       # "prod" | "testnet"
WS_URL         = f"wss://ws.api.{ENV}.paradex.trade/v1"

MARKET         = "ETH-USD-PERP"
REFRESH_RATE   = "50ms"                                 # 15 ур., 50 мс
COMM_RATE      = decimal.Decimal("0.0005")              # 0.05 %
PROFIT_MIN_USD = decimal.Decimal("1")

JWT_TOKEN      = get_jwt()
TOKEN_TTL      = 60 * 25                                # 25 мин до обновления

OB_CHANNEL     = f"order_book.{MARKET}.snapshot@15@{REFRESH_RATE}"

# ───────────────────────-- helpers ───────────────────────
_msg_id = 0
def next_id() -> int:
    global _msg_id
    _msg_id += 1
    return _msg_id

def now(): return time.strftime("%H:%M:%S")

def log(*a, **k): print(now(), *a, **k)

# ───────────────────────-- WS callbacks ───────────────────────
AUTH_REQ_ID = None      # запомним id auth-сообщения

def on_open(ws):
    global AUTH_REQ_ID
    log("▶ WS connected → sending auth …")
    AUTH_REQ_ID = next_id()
    ws.send(json.dumps({
        "jsonrpc": "2.0",
        "method": "auth",
        "params": {"bearer": JWT_TOKEN},
        "id": AUTH_REQ_ID,
    }))

def subscribe_private_and_public(ws):
    for ch in (OB_CHANNEL, "account"):
        ws.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "subscribe",
            "params": {"channel": ch},
            "id": next_id(),
        }))
    log(f"✅ Subscribed: {OB_CHANNEL}  &  account")

def on_message(ws, message):
    msg = json.loads(message)

    # DEBUG: покажем всё, что пришло (раскомментируйте при необходимости)
    # print("RAW:", msg)

    # 1) Обработка ответа на auth
    if msg.get("id") == AUTH_REQ_ID:
        if "result" in msg:           # успех: server отвечает {"result":{}}
            log("✅ Auth success")
            subscribe_private_and_public(ws)
        else:                         # ошибка авторизации
            log("❌ Auth failure:", msg)
            ws.close()
        return

    # 2) Ошибки без id (например, подписка без auth)
    if "error" in msg:
        log("❌ Server error:", msg["error"])
        return

    # 3) Интересуют только push-уведомления
    if msg.get("method") != "subscription":
        return

    chan   = msg["params"]["channel"]
    data   = msg["params"]["data"]

    # 3-a) account → баланс
    if chan == "account":
        fc = decimal.Decimal(data["free_collateral"])
        log(f"💰  Free collateral: {fc} {data['settlement_asset']}")
        return

    # 3-b) order-book
    if chan != OB_CHANNEL:
        return

    bids = [(decimal.Decimal(o["size"]), decimal.Decimal(o["price"]))
            for o in data.get("inserts", []) if o["side"] == "BUY"]
    asks = [(decimal.Decimal(o["size"]), decimal.Decimal(o["price"]))
            for o in data.get("inserts", []) if o["side"] == "SELL"]

    for q_b, p_b in bids:
        for q_a, p_a in asks:
            if q_b != q_a or p_a <= p_b:
                continue
            gross = (p_a - p_b) * q_b
            fees  = (p_b * q_b + p_a * q_a) * COMM_RATE
            net   = gross - fees
            if net > PROFIT_MIN_USD:
                log(f"🚀  BUY {q_b}@{p_b} → SELL@{p_a}  "
                    f"Δ={p_a - p_b:.2f}  Net={net:.2f}")

def on_error(ws, err):   log("✖ WS error:", err)
def on_close(ws, c, r):  log(f"✖ WS closed: {c} / {r}")

# ───────────────────────-- main loop ───────────────────────
if __name__ == "__main__":
    next_token_at = time.time() + TOKEN_TTL

    while True:
        ws = websocket.WebSocketApp(
            WS_URL,
            on_open   = on_open,
            on_message= on_message,
            on_error  = on_error,
            on_close  = on_close,
        )
        # держим соединение; run_forever вернёт управление после закрытия
        ws.run_forever(ping_interval=20, ping_timeout=10)

        # ♦ если здесь — значит соединение упало; проверяем, пора ли обновлять JWT
        if time.time() >= next_token_at:
            log("🔄  Refreshing JWT …")
            JWT_TOKEN = get_jwt()
            next_token_at = time.time() + TOKEN_TTL

        time.sleep(2)    # небольшая пауза перед реконнектом
