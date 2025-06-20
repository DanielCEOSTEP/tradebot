import sys
import os
import types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio
from decimal import Decimal
import pytest

# Create minimal stubs so arbitrage_bot can be imported without installing
# external dependencies.
paradex_module = types.ModuleType("paradex_py.paradex")
class Paradex:
    pass
paradex_module.Paradex = Paradex
common_module = types.ModuleType("paradex_py.common.order")

class Order:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

class OrderSide:
    Buy = "BUY"
    Sell = "SELL"

class OrderType:
    Limit = "LIMIT"

common_module.Order = Order
common_module.OrderSide = OrderSide
common_module.OrderType = OrderType

ws_module = types.ModuleType("paradex_py.api.ws_client")
class ParadexWebsocketChannel:
    ACCOUNT = "ACCOUNT"
    ORDERS = "ORDERS"

ws_module.ParadexWebsocketChannel = ParadexWebsocketChannel

sys.modules["paradex_py"] = types.ModuleType("paradex_py")
sys.modules["paradex_py.paradex"] = paradex_module
sys.modules["paradex_py.common"] = types.ModuleType("paradex_py.common")
sys.modules["paradex_py.common.order"] = common_module
sys.modules["paradex_py.api"] = types.ModuleType("paradex_py.api")
sys.modules["paradex_py.api.ws_client"] = ws_module

import arbitrage_bot

class DummyAPIClient:
    def __init__(self):
        self.submitted = False
        self._positions = {"results": []}

    def fetch_positions(self):
        return self._positions

    def submit_orders_batch(self, orders):
        self.submitted = True

class DummyWSClient:
    callbacks = {}
    subscribed_channels = {}

class DummyParadex:
    def __init__(self, *args, **kwargs):
        self.api_client = DummyAPIClient()
        self.ws_client = DummyWSClient()

@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setattr(arbitrage_bot, "Paradex", DummyParadex)
    monkeypatch.setenv("PARADEX_L1_ADDRESS", "0xabc")
    monkeypatch.setenv("PARADEX_L1_PRIVATE_KEY", "0x1")
    monkeypatch.setenv("PARADEX_MARKET", "ETH-USD-PERP")
    cfg = arbitrage_bot.load_config()
    cfg["min_profit_usd"] = Decimal("1")
    b = arbitrage_bot.ArbitrageBot(cfg)
    b.available_balance_usd = Decimal("1000")
    async def no_balance(self):
        return None
    monkeypatch.setattr(b, "refresh_balance", no_balance.__get__(b))
    return b

async def prepare_bot(bot, positions, monkeypatch):
    bot.paradex.api_client._positions = positions
    bot.best_bid = Decimal("100")
    bot.best_bid_qty = Decimal("1")
    bot.best_ask = Decimal("90")
    bot.best_ask_qty = Decimal("1")
    placed = {}
    async def fake_place_orders(self, ask, bid, size, direction="long"):
        placed["order"] = (ask, bid, size, direction)
    monkeypatch.setattr(bot, "place_orders", fake_place_orders.__get__(bot))
    await bot.refresh_positions()
    return placed



@pytest.mark.asyncio
async def test_scan_inversions_buy_sell(bot, monkeypatch):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        q = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, q, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    bot.cfg["min_profit_usd"] = Decimal("0")
    inserts = [
        {"side": "BUY", "size": "0.5", "price": "100"},
        {"side": "SELL", "size": "0.5", "price": "101"},
    ]
    await bot.scan_inversions(inserts)
    assert captured["order"] == (
        Decimal("100"),
        Decimal("101"),
        Decimal("0.5"),
        "long",
    )


@pytest.mark.asyncio
async def test_scan_inversions_sell_buy(bot, monkeypatch):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        q = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, q, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    inserts = [
        {"side": "SELL", "size": "1", "price": "2554"},
        {"side": "BUY", "size": "1", "price": "2551"},
    ]
    await bot.scan_inversions(inserts)
    assert captured["order"] == (
        Decimal("2551"),
        Decimal("2554"),
        Decimal("1"),
        "long",
    )


@pytest.mark.asyncio
async def test_scan_inversions_zero_delta(bot, monkeypatch):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        q = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, q, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    inserts = [
        {"side": "SELL", "size": "1", "price": "2551"},
        {"side": "BUY", "size": "1", "price": "2551"},
    ]
    await bot.scan_inversions(inserts)
    assert not captured


@pytest.mark.asyncio
async def test_scan_inversions_size_mismatch(bot, monkeypatch):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        q = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, q, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    inserts = [
        {"side": "BUY", "size": "2", "price": "2553"},
        {"side": "SELL", "size": "1", "price": "2555"},
    ]
    await bot.scan_inversions(inserts)
    assert captured["order"] == (
        Decimal("2553"),
        Decimal("2555"),
        Decimal("1"),
        "long",
    )



def test_scan_full_book(monkeypatch, bot):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        size = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, size, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    bot.cfg["min_profit"] = Decimal("1")
    bids = [["102", "1"], ["101", "1"]]
    asks = [["100", "1"], ["99", "1"]]
    asyncio.run(bot.scan_full_book(bids, asks))
    assert captured["order"] == (
        Decimal("100"),
        Decimal("102"),
        Decimal("1"),
        "long",
    )


def test_scan_full_book_short(monkeypatch, bot):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        size = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, size, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    bot.cfg["min_profit"] = Decimal("1")
    bids = [["100", "1"]]
    asks = [["102", "1"]]
    asyncio.run(bot.scan_full_book(bids, asks))
    assert captured["order"] == (
        Decimal("100"),
        Decimal("102"),
        Decimal("1"),
        "short",
    )


def test_scan_full_book_profit_filter(monkeypatch, bot):
    captured = {}

    async def fake_handle(self, *args, **kwargs):
        pb = kwargs.get("price_buy", args[0] if len(args) > 0 else None)
        ps = kwargs.get("price_sell", args[1] if len(args) > 1 else None)
        size = kwargs.get("size", args[2] if len(args) > 2 else None)
        direction = kwargs.get("direction", args[3] if len(args) > 3 else "long")
        captured["order"] = (pb, ps, size, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    bot.cfg["min_profit"] = Decimal("0")
    bot.cfg["min_profit_usd"] = Decimal("10")
    bids = [["102", "1"]]
    asks = [["100", "1"]]
    asyncio.run(bot.scan_full_book(bids, asks))
    assert not captured
