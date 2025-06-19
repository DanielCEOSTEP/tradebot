import importlib
import sys
import types

import pytest


def import_place_order(monkeypatch):
    dummy = types.ModuleType("paradex_py")
    dummy_paradex = types.ModuleType("paradex_py.paradex")
    dummy_common = types.ModuleType("paradex_py.common")
    dummy_order = types.ModuleType("paradex_py.common.order")
    dummy_paradex.Paradex = object
    dummy_order.Order = object
    dummy_order.OrderSide = object
    dummy_order.OrderType = object
    monkeypatch.setitem(sys.modules, "paradex_py", dummy)
    monkeypatch.setitem(sys.modules, "paradex_py.paradex", dummy_paradex)
    monkeypatch.setitem(sys.modules, "paradex_py.common", dummy_common)
    monkeypatch.setitem(sys.modules, "paradex_py.common.order", dummy_order)
    return importlib.import_module("place_order")


def test_load_config_from_env(monkeypatch):
    place_order = import_place_order(monkeypatch)
    monkeypatch.setenv("PARADEX_L1_ADDRESS", "0xabc")
    monkeypatch.setenv("PARADEX_L1_PRIVATE_KEY", "key")
    monkeypatch.setenv("PARADEX_ENV", "mainnet")

    importlib.reload(place_order)
    cfg = place_order.load_config()
    assert cfg["env"] == "mainnet"
    assert cfg["l1_address"] == "0xabc"
    assert cfg["l1_private_key"] == "key"


def test_load_config_missing_required(monkeypatch):
    place_order = import_place_order(monkeypatch)
    monkeypatch.delenv("PARADEX_L1_ADDRESS", raising=False)
    monkeypatch.setenv("PARADEX_L1_PRIVATE_KEY", "key")
    importlib.reload(place_order)
    with pytest.raises(SystemExit):
        place_order.load_config()

