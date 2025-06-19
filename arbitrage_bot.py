import asyncio
import logging
import os
from decimal import Decimal
from uuid import uuid4
from typing import Dict, Optional
from datetime import datetime

import tomli
import yaml
from dotenv import load_dotenv

from paradex_py.paradex import Paradex
from paradex_py.common.order import Order, OrderSide, OrderType
from paradex_py.api.ws_client import ParadexWebsocketChannel

ENV_MAP = {
    "PARADEX_ENV": "env",
    "PARADEX_L1_ADDRESS": "l1_address",
    "PARADEX_L1_PRIVATE_KEY": "l1_private_key",
    "PARADEX_L2_PRIVATE_KEY": "l2_private_key",
    "PARADEX_MARKET": "market",
    "PARADEX_ORDER_SIZE": "order_size",  # optional max order size
    "PARADEX_MIN_PROFIT_USD": "min_profit_usd",
    "PARADEX_FEE_PCT": "fee_pct",
    "PARADEX_MAX_OPEN_ORDERS": "max_open_orders",
    "PARADEX_BALANCE_RESERVED_PCT": "balance_reserved_pct",
    "PARADEX_POLL_INTERVAL_MS": "poll_interval_ms",
    "PARADEX_LOG_LEVEL": "log_level",
    "PARADEX_BALANCE_REFRESH_SEC": "balance_refresh_sec",
}


def load_config(config_path: Optional[str] = None) -> Dict:
    """Load configuration from optional toml/yaml file and environment."""
    load_dotenv()
    cfg: Dict[str, str] = {}
    if config_path and os.path.exists(config_path):
        if config_path.endswith(".toml"):
            with open(config_path, "rb") as f:
                cfg.update(tomli.load(f))
        elif config_path.endswith((".yaml", ".yml")):
            with open(config_path, "r") as f:
                cfg.update(yaml.safe_load(f))
    for env, key in ENV_MAP.items():
        if os.getenv(env) is not None:
            cfg[key] = os.getenv(env)
    cfg.setdefault("env", "testnet")
    for k in ["order_size", "min_profit_usd", "fee_pct"]:
        if k in cfg:
            cfg[k] = Decimal(cfg[k])
    cfg["max_open_orders"] = int(cfg.get("max_open_orders", 1))
    cfg["balance_reserved_pct"] = float(cfg.get("balance_reserved_pct", 1.0))
    cfg["poll_interval_ms"] = int(cfg.get("poll_interval_ms", 1000))
    cfg["balance_refresh_sec"] = int(cfg.get("balance_refresh_sec", 30))
    cfg.setdefault("min_profit_usd", Decimal("1"))
    cfg.setdefault("fee_pct", Decimal("0.001"))
    cfg.setdefault("log_level", "INFO")
    required = ["l1_address", "market"]
    missing = [r for r in required if r not in cfg]
    if missing:
        raise SystemExit(f"Missing required config values: {', '.join(missing)}")
    if not cfg.get("l1_private_key") and not cfg.get("l2_private_key"):
        raise SystemExit("Provide PARADEX_L1_PRIVATE_KEY or PARADEX_L2_PRIVATE_KEY")
    return cfg


class ArbitrageBot:
    def __init__(self, cfg: Dict) -> None:
        self.cfg = cfg
        self.logger = logging.getLogger("arbitrage_bot")
        self.logger.setLevel(getattr(logging, cfg["log_level"].upper()))
        self.paradex = Paradex(
            env=cfg["env"],
            l1_address=cfg["l1_address"],
            l1_private_key=cfg.get("l1_private_key"),
            l2_private_key=cfg.get("l2_private_key"),
            logger=self.logger,
        )
        self.best_bid: Optional[Decimal] = None
        self.best_bid_qty: Optional[Decimal] = None
        self.best_ask: Optional[Decimal] = None
        self.best_ask_qty: Optional[Decimal] = None
        self.available_balance_usd: Decimal = Decimal("0")
        self.open_batches: Dict[str, Dict[str, str]] = {}

    async def refresh_balance(self) -> None:
        summary = await asyncio.to_thread(
            self.paradex.api_client.fetch_account_summary
        )
        if getattr(summary, "free_collateral", None):
            self.available_balance_usd = Decimal(summary.free_collateral)
        self.logger.debug("Balance refreshed: %s", self.available_balance_usd)

    async def on_account_update(self, _channel, _message) -> None:
        await self.refresh_balance()

    async def on_order_update(self, _channel, message) -> None:
        data = message.get("params", {}).get("data", {})
        client_id = data.get("client_id")
        status = data.get("status")
        for batch_id, ids in list(self.open_batches.items()):
            if client_id in ids.values() and status in {"FILLED", "CANCELLED"}:
                del self.open_batches[batch_id]
                self.logger.info("Order %s %s", client_id, status)

    async def on_order_book(self, _channel, message) -> None:
        self.logger.debug("Order book message: %s", message)
        data = message.get("params", {}).get("data", {})
        bids = data.get("bids")
        asks = data.get("asks")
        if not bids or not asks:
            return
        self.best_bid = Decimal(bids[0][0])
        self.best_bid_qty = Decimal(bids[0][1])
        self.best_ask = Decimal(asks[0][0])
        self.best_ask_qty = Decimal(asks[0][1])
        await self.check_inversion()

    async def check_inversion(self) -> None:
        if self.best_bid is None or self.best_ask is None:
            return
        if self.best_bid_qty is None or self.best_ask_qty is None:
            return
        order_size = min(self.best_bid_qty, self.best_ask_qty)
        if "order_size" in self.cfg:
            order_size = min(order_size, self.cfg["order_size"])
        delta = self.best_bid - self.best_ask
        fees = (self.best_bid + self.best_ask) * order_size * self.cfg["fee_pct"]
        profit = delta * order_size - fees
        if profit < self.cfg["min_profit_usd"]:
            return

        signal = (
            f"{datetime.now().strftime('%H:%M:%S')} \U0001F680  BUY {order_size:.2f}@{self.best_ask} "
            f"\u2192 SELL@{self.best_bid}  \u0394={delta:.2f}  Net={profit:.2f}"
        )
        self.logger.info(signal)

        await self.refresh_balance()
        usd_needed = self.best_ask * order_size
        if usd_needed > self.available_balance_usd * Decimal(str(self.cfg["balance_reserved_pct"])):
            self.logger.info("\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0431\u0430\u043b\u0430\u043d\u0441\u0430")
            return
        if len(self.open_batches) >= self.cfg["max_open_orders"]:
            self.logger.warning("Max open orders reached")
            return
        self.logger.info("\u0420\u0430\u0437\u043c\u0435\u0449\u0430\u044e \u043e\u0440\u0434\u0435\u0440\u0430")
        await self.place_orders(self.best_ask, self.best_bid, order_size)

    async def place_orders(self, price_buy: Decimal, price_sell: Decimal, size: Decimal) -> None:
        batch_id = str(uuid4())
        orders = [
            Order(
                market=self.cfg["market"],
                order_type=OrderType.Limit,
                order_side=OrderSide.Buy,
                size=size,
                limit_price=price_buy,
                client_id=f"{batch_id}-buy",
                time_in_force="GTC",
            ),
            Order(
                market=self.cfg["market"],
                order_type=OrderType.Limit,
                order_side=OrderSide.Sell,
                size=size,
                limit_price=price_sell,
                client_id=f"{batch_id}-sell",
                post_only=True,
            ),
        ]
        try:
            self.paradex.api_client.submit_orders_batch(orders)
            self.open_batches[batch_id] = {"buy": orders[0].client_id, "sell": orders[1].client_id}
            self.logger.info(
                "\u0420\u0430\u0437\u043c\u0435\u0449\u0435\u043d\u044b \u043e\u0440\u0434\u0435\u0440\u0430: BUY %s @ %s \u2192 SELL @ %s",
                size,
                price_buy,
                price_sell,
            )
        except Exception as exc:
            self.logger.error("Failed to submit orders: %s", exc)

    async def balance_refresher(self) -> None:
        while True:
            await self.refresh_balance()
            await asyncio.sleep(self.cfg["balance_refresh_sec"])

    async def run(self) -> None:
        await self.refresh_balance()
        await self.paradex.ws_client.connect()
        # Subscribe to order book snapshot channel with desired depth
        book_channel = f"order_book_snapshot.{self.cfg['market']}.50"
        self.paradex.ws_client.callbacks[book_channel] = self.on_order_book
        await self.paradex.ws_client._subscribe_to_channel_by_name(book_channel)
        while not self.paradex.ws_client.subscribed_channels.get(book_channel):
            await asyncio.sleep(0.1)
        self.logger.info("Subscription acknowledged: %s", book_channel)
        await self.paradex.ws_client.subscribe(
            ParadexWebsocketChannel.ACCOUNT,
            self.on_account_update,
        )
        await self.paradex.ws_client.subscribe(
            ParadexWebsocketChannel.ORDERS,
            self.on_order_update,
            params={"market": self.cfg["market"]},
        )
        asyncio.create_task(self.balance_refresher())
        while True:
            await asyncio.sleep(self.cfg["poll_interval_ms"] / 1000)


async def amain(config_path: Optional[str] = None) -> None:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, cfg["log_level"].upper()),
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    logging.getLogger("paradex_py").setLevel(logging.WARNING)
    bot = ArbitrageBot(cfg)
    await bot.run()


def main() -> None:
    config_path = os.getenv("PARADEX_CONFIG")
    asyncio.run(amain(config_path))


if __name__ == "__main__":
    main()
