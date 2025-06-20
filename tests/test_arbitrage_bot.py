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
    async def fake_place_orders(self, ask, bid, size):
        placed["order"] = (ask, bid, size)
    monkeypatch.setattr(bot, "place_orders", fake_place_orders.__get__(bot))
    await bot.refresh_positions()
    return placed

@pytest.mark.asyncio
async def test_check_inversion_no_open(bot, monkeypatch):
    placed = await prepare_bot(bot, {"results": []}, monkeypatch)
    await bot.check_inversion()
    assert placed

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
    assert placed

@pytest.mark.asyncio
async def test_check_inversion_closed_negative(bot, monkeypatch):
    positions = {"results": [{"market": "ETH-USD-PERP", "status": "CLOSED", "closed_at": 1, "realized_positional_pnl": "-1"}]}
    placed = await prepare_bot(bot, positions, monkeypatch)
    await bot.check_inversion()
    assert not placed
