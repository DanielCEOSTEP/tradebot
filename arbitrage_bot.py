import asyncio
import logging
import os
from decimal import Decimal
from uuid import uuid4
from typing import Dict, Optional
from datetime import datetime

import tomli
import yaml
import decimal
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
    "PARADEX_LEVERAGE": "leverage",  # optional leverage factor
    "PARADEX_MIN_PROFIT_USD": "min_profit_usd",
    "PARADEX_MIN_PROFIT": "min_profit",
    "PARADEX_TAKER_FEE_PCT": "taker_fee_pct",
    "PARADEX_MAKER_FEE_PCT": "maker_fee_pct",
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

    # Backwards compatibility: if legacy PARADEX_FEE_PCT is provided and
    # taker/maker fees are not explicitly set, use it as a fallback value.
    fee_pct_env = os.getenv("PARADEX_FEE_PCT")
    if fee_pct_env is not None:
        cfg.setdefault("taker_fee_pct", fee_pct_env)
        cfg.setdefault("maker_fee_pct", fee_pct_env)
    cfg.setdefault("env", "testnet")
    for k in [
        "order_size",
        "min_profit_usd",
        "min_profit",
        "taker_fee_pct",
        "maker_fee_pct",
        "leverage",
        "fee_pct",
    ]:
        if k in cfg:
            cfg[k] = Decimal(cfg[k])

    # Fallback from config file for legacy fee_pct option
    if "fee_pct" in cfg:
        cfg.setdefault("taker_fee_pct", cfg["fee_pct"])
        cfg.setdefault("maker_fee_pct", cfg["fee_pct"])
        cfg.pop("fee_pct")
    cfg["max_open_orders"] = int(cfg.get("max_open_orders", 1))
    cfg["balance_reserved_pct"] = float(cfg.get("balance_reserved_pct", 1.0))
    cfg["poll_interval_ms"] = int(cfg.get("poll_interval_ms", 1000))
    cfg["balance_refresh_sec"] = int(cfg.get("balance_refresh_sec", 30))
    cfg.setdefault("min_profit_usd", Decimal("1"))
    cfg.setdefault("min_profit", Decimal("0"))
    cfg.setdefault("leverage", Decimal("1"))
    cfg.setdefault("taker_fee_pct", Decimal("0.0003"))
    cfg.setdefault("maker_fee_pct", Decimal("-0.00005"))
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
        self.has_open_position: bool = False
        self.last_position_pnl: Optional[Decimal] = None
        self.open_position_price: Optional[Decimal] = None

    async def refresh_balance(self) -> None:
        summary = await asyncio.to_thread(
            self.paradex.api_client.fetch_account_summary
        )
        if getattr(summary, "free_collateral", None):
            self.available_balance_usd = Decimal(summary.free_collateral)
        self.logger.debug("Balance refreshed: %s", self.available_balance_usd)

    async def refresh_positions(self) -> None:
        """Refresh open position status, entry price, and last closed PnL."""
        try:
            data = await asyncio.to_thread(self.paradex.api_client.fetch_positions)
        except Exception as exc:
            self.logger.error("Failed to fetch positions: %s", exc)
            return
        results = data.get("results", []) if isinstance(data, dict) else []
        self.has_open_position = False
        self.open_position_price = None
        latest_time = None
        latest_pnl = None
        for pos in results:
            if pos.get("market") != self.cfg["market"]:
                continue
            status = pos.get("status")
            if status == "OPEN":
                self.has_open_position = True
                price = (
                    pos.get("entry_price")
                    or pos.get("avg_entry_price")
                    or pos.get("open_price")
                    or pos.get("price")
                )
                try:
                    if price is not None:
                        self.open_position_price = Decimal(str(price))
                except (TypeError, decimal.InvalidOperation):
                    self.open_position_price = None
                return
            if status == "CLOSED":
                closed_at = pos.get("closed_at") or pos.get("last_updated_at")
                if closed_at is not None and (latest_time is None or closed_at > latest_time):
                    latest_time = closed_at
                    try:
                        pnl_str = pos.get("realized_positional_pnl") or "0"
                        latest_pnl = Decimal(pnl_str)
                    except (TypeError, decimal.InvalidOperation):
                        latest_pnl = Decimal("0")
        if latest_pnl is not None:
            self.last_position_pnl = latest_pnl

    async def on_account_update(self, _channel, _message) -> None:
        await self.refresh_balance()
        await self.refresh_positions()

    async def on_order_update(self, _channel, message) -> None:
        data = message.get("params", {}).get("data", {})
        client_id = data.get("client_id")
        status = data.get("status")
        for batch_id, ids in list(self.open_batches.items()):
            if client_id in ids.values() and status in {"FILLED", "CANCELLED"}:
                del self.open_batches[batch_id]
                self.logger.info("Order %s %s", client_id, status)
        await self.refresh_positions()

    async def on_order_book(self, _channel, message) -> None:
        # Avoid logging the entire order book message to prevent flooding the
        # terminal. The data is still processed below.
        data = message.get("params", {}).get("data", {})
        bids = data.get("bids")
        asks = data.get("asks")

        if bids and asks:
            self.best_bid = Decimal(bids[0][0])
            self.best_bid_qty = Decimal(bids[0][1])
            self.best_ask = Decimal(asks[0][0])
            self.best_ask_qty = Decimal(asks[0][1])
            await self.scan_full_book(bids, asks)

        inserts = data.get("inserts", [])
        if inserts:
            await self.scan_inversions(inserts)

        for entry in inserts:
            try:
                price = Decimal(entry["price"])
                size = Decimal(entry["size"])
                side = entry["side"]
            except (KeyError, TypeError, decimal.InvalidOperation):
                continue

            if side == "BUY" and (self.best_bid is None or price > self.best_bid):
                self.best_bid = price
                self.best_bid_qty = size
            elif side == "SELL" and (self.best_ask is None or price < self.best_ask):
                self.best_ask = price
                self.best_ask_qty = size



    async def scan_full_book(self, bids, asks) -> None:
        """Scan the entire order book for price inversions."""
        if not bids or not asks:
            return
        if self.has_open_position:
            return
        min_profit = self.cfg.get("min_profit", Decimal("0"))
        for bid in bids:
            for ask in asks:
                try:
                    bid_price = Decimal(bid[0])
                    bid_size = Decimal(bid[1])
                    ask_price = Decimal(ask[0])
                    ask_size = Decimal(ask[1])
                except (IndexError, TypeError, decimal.InvalidOperation):
                    continue
                size = min(bid_size, ask_size)
                taker_fee = self.cfg["taker_fee_pct"]
                maker_fee = self.cfg["maker_fee_pct"]
                long_profit = (
                    (bid_price - ask_price) * size
                    - ask_price * size * taker_fee
                    - bid_price * size * maker_fee
                )
                short_profit = (
                    (ask_price - bid_price) * size
                    - bid_price * size * taker_fee
                    - ask_price * size * maker_fee
                )
                profit_long_ok = long_profit >= self.cfg["min_profit_usd"]
                profit_short_ok = short_profit >= self.cfg["min_profit_usd"]
                if bid_price >= ask_price + min_profit and profit_long_ok:
                    await self.handle_order(ask_price, bid_price, size, "long")
                    return
                elif ask_price >= bid_price + min_profit and profit_short_ok:
                    lower_bid = bid_price
                    higher_ask = ask_price
                    await self.handle_order(
                        price_buy=lower_bid,
                        price_sell=higher_ask,
                        size=size,
                        direction="short",
                    )
                    return


    async def scan_inversions(self, inserts) -> None:
        """Check for simple two-order cross spreads regardless of order."""
        if len(inserts) < 2:
            return

        first, second = inserts[0], inserts[1]
        try:
            p1 = Decimal(first["price"])
            q1 = Decimal(first["size"])
            s1 = first["side"]
            p2 = Decimal(second["price"])
            q2 = Decimal(second["size"])
            s2 = second["side"]
        except (KeyError, TypeError, decimal.InvalidOperation):
            return

        size = min(q1, q2)

        if s1 == "BUY" and s2 == "SELL":
            price_buy, price_sell = p1, p2
        elif s1 == "SELL" and s2 == "BUY":
            price_buy, price_sell = p2, p1
        else:
            return

        if price_sell <= price_buy:
            return

        long_profit = (price_sell - price_buy) * size - (
            price_buy * size * self.cfg.get("taker_fee_pct", Decimal("0"))
            + price_sell * size * self.cfg.get("maker_fee_pct", Decimal("0"))
        )
        short_profit = (price_sell - price_buy) * size - (
            price_sell * size * self.cfg.get("taker_fee_pct", Decimal("0"))
            + price_buy * size * self.cfg.get("maker_fee_pct", Decimal("0"))
        )

        if long_profit >= short_profit and long_profit >= self.cfg.get("min_profit_usd", Decimal("0")):
            direction = "long"
            net = long_profit
        elif short_profit >= self.cfg.get("min_profit_usd", Decimal("0")):
            direction = "short"
            net = short_profit
        else:
            return

        signal = (
            f"{datetime.now().strftime('%H:%M:%S')} \U0001F680  {direction.upper()} {size:.2f}@{price_buy if direction=='long' else price_sell} "
            f"\u2192 {'SELL' if direction=='long' else 'BUY'}@{price_sell if direction=='long' else price_buy}  \u0394={price_sell - price_buy:.2f}  Net={net:.2f}"
        )
        print(signal)
        if direction == "short":
            lower_bid = price_buy
            higher_ask = price_sell
            await self.handle_order(
                price_buy=lower_bid,
                price_sell=higher_ask,
                size=size,
                direction=direction,
            )
        else:
            await self.handle_order(price_buy, price_sell, size, direction)

    async def handle_order(
        self, price_buy: Decimal, price_sell: Decimal, size: Decimal, direction: str = "long"
    ) -> None:
        await self.refresh_balance()
        if self.has_open_position:
            if self.open_position_price is not None:
                self.logger.info(
                    "Open position at %s, skipping new orders",
                    self.open_position_price,
                )
            else:
                self.logger.info("Open position detected, skipping new orders")
            return
        if "order_size" in self.cfg:
            size = min(size, self.cfg["order_size"])
        leverage = self.cfg.get("leverage", Decimal("1"))
        first_price = price_buy if direction == "long" else price_sell
        margin_needed = (first_price * size) / leverage
        if margin_needed > self.available_balance_usd * Decimal(str(self.cfg["balance_reserved_pct"])):
            self.logger.info("\u041d\u0435\u0434\u043e\u0441\u0442\u0430\u0442\u043e\u0447\u043d\u043e \u0431\u0430\u043b\u0430\u043d\u0441\u0430")
            return
        if len(self.open_batches) >= self.cfg["max_open_orders"]:
            self.logger.warning("Max open orders reached")
            return
        self.logger.info("\u0420\u0430\u0437\u043c\u0435\u0449\u0430\u044e \u043e\u0440\u0434\u0435\u0440\u0430")
        await self.place_orders(price_buy, price_sell, size, direction)

    async def place_orders(
        self, price_buy: Decimal, price_sell: Decimal, size: Decimal, direction: str = "long"
    ) -> None:
        batch_id = str(uuid4())
        if direction == "long":
            order_buy = Order(
                market=self.cfg["market"],
                order_type=OrderType.Limit,
                order_side=OrderSide.Buy,
                size=size,
                limit_price=price_buy,
                client_id=f"{batch_id}-buy",
                instruction="GTC",
            )
            order_sell = Order(
                market=self.cfg["market"],
                order_type=OrderType.Limit,
                order_side=OrderSide.Sell,
                size=size,
                limit_price=price_sell,
                client_id=f"{batch_id}-sell",
                instruction="GTC",
            )
            orders = [order_buy, order_sell]
        else:
            order_sell = Order(
                market=self.cfg["market"],
                order_type=OrderType.Limit,
                order_side=OrderSide.Sell,
                size=size,
                limit_price=price_sell,
                client_id=f"{batch_id}-sell",
                instruction="GTC",
            )
            order_buy = Order(
                market=self.cfg["market"],
                order_type=OrderType.Limit,
                order_side=OrderSide.Buy,
                size=size,
                limit_price=price_buy,
                client_id=f"{batch_id}-buy",
                instruction="GTC",
            )
            orders = [order_sell, order_buy]
        try:
            await asyncio.to_thread(
                self.paradex.api_client.submit_orders_batch, orders
            )
            self.open_batches[batch_id] = {"buy": order_buy.client_id, "sell": order_sell.client_id}
            if direction == "long":
                self.logger.info(
                    "\u0420\u0430\u0437\u043c\u0435\u0449\u0435\u043d\u044b \u043e\u0440\u0434\u0435\u0440\u0430: BUY %s @ %s \u2192 SELL @ %s",
                    size,
                    price_buy,
                    price_sell,
                )
            else:
                self.logger.info(
                    "\u0420\u0430\u0437\u043c\u0435\u0449\u0435\u043d\u044b \u043e\u0440\u0434\u0435\u0440\u0430: SELL %s @ %s \u2192 BUY @ %s",
                    size,
                    price_sell,
                    price_buy,
                )
        except Exception as exc:
            self.logger.error("Failed to submit orders: %s", exc)

    async def balance_refresher(self) -> None:
        while True:
            await self.refresh_balance()
            await self.refresh_positions()
            await asyncio.sleep(self.cfg["balance_refresh_sec"])

    async def run(self) -> None:
        await self.refresh_balance()
        await self.refresh_positions()
        await self.paradex.ws_client.connect()
        # Subscribe to the order book snapshot channel with the same pattern as
        # used in ``paradex_bot.py``. This channel streams updates every 50ms
        # with a depth of 15 levels.
        # The SDK expects ``order_book.{market}.snapshot@15@50ms`` for the
        # fast snapshot channel. The enum value in ``paradex_py`` uses a dot
        # before the refresh rate which does not work for this channel, so
        # subscribe manually using the expected string.
        book_channel = f"order_book.{self.cfg['market']}.snapshot@15@50ms"
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
