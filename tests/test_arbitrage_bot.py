import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio
from decimal import Decimal
import pytest

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
async def test_check_inversion_no_open(bot, monkeypatch):
    placed = await prepare_bot(bot, {"results": []}, monkeypatch)
    await bot.check_inversion()
    assert placed["order"][3] == "long"

@pytest.mark.asyncio
async def test_check_inversion_open_position(bot, monkeypatch):
    positions = {"results": [{"market": "ETH-USD-PERP", "status": "OPEN", "entry_price": "95"}]}
    placed = await prepare_bot(bot, positions, monkeypatch)
    await bot.check_inversion()
    assert not placed

@pytest.mark.asyncio
async def test_check_inversion_closed_positive(bot, monkeypatch):
    positions = {"results": [{"market": "ETH-USD-PERP", "status": "CLOSED", "closed_at": 1, "realized_positional_pnl": "5"}]}
    placed = await prepare_bot(bot, positions, monkeypatch)
    await bot.check_inversion()
    assert placed["order"][3] == "long"

@pytest.mark.asyncio
async def test_check_inversion_closed_negative(bot, monkeypatch):
    positions = {"results": [{"market": "ETH-USD-PERP", "status": "CLOSED", "closed_at": 1, "realized_positional_pnl": "-1"}]}
    placed = await prepare_bot(bot, positions, monkeypatch)
    await bot.check_inversion()
    assert placed["order"][3] == "long"


@pytest.mark.asyncio
async def test_scan_inversions_buy_sell(bot, monkeypatch):
    captured = {}

    async def fake_handle(self, pb, ps, q, direction="long"):
        captured["order"] = (pb, ps, q, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
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

    async def fake_handle(self, pb, ps, q, direction="long"):
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

    async def fake_handle(self, pb, ps, q, direction="long"):
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

    async def fake_handle(self, pb, ps, q, direction="long"):
        captured["order"] = (pb, ps, q, direction)

    monkeypatch.setattr(bot, "handle_order", fake_handle.__get__(bot))
    inserts = [
        {"side": "BUY", "size": "2", "price": "2553"},
        {"side": "SELL", "size": "1", "price": "2555"},
    ]
    await bot.scan_inversions(inserts)
    assert not captured


@pytest.mark.asyncio
async def test_check_inversion_short_direction(bot, monkeypatch):
    placed = await prepare_bot(bot, {"results": []}, monkeypatch)
    bot.cfg["taker_fee_pct"] = Decimal("0.0001")
    bot.cfg["maker_fee_pct"] = Decimal("0.0002")
    await bot.check_inversion()
    assert placed["order"][3] == "short"


def test_scan_full_book(monkeypatch, bot):
    captured = {}

    async def fake_handle(self, ask_price, bid_price, size, direction="long"):
        captured["order"] = (ask_price, bid_price, size, direction)

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
