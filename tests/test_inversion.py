from decimal import Decimal
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from market_utils import check_inversion


def test_check_inversion_profit():
    book = {
        "bids": [{"price": "101"}],
        "asks": [{"price": "100"}],
    }
    assert check_inversion(book) == Decimal("1")


def test_check_inversion_no_profit():
    book = {
        "bids": [{"price": "99"}],
        "asks": [{"price": "100"}],
    }
    assert check_inversion(book) == Decimal("0")

