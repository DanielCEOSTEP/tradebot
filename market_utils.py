from decimal import Decimal


def check_inversion(order_book: dict) -> Decimal:
    """Return profit if best bid is higher than best ask.

    Profit is calculated as the difference between the best bid and best ask
    prices. If there is no inversion return ``Decimal('0')``.
    """
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    if not bids or not asks:
        return Decimal("0")

    best_bid = Decimal(str(bids[0]["price"]))
    best_ask = Decimal(str(asks[0]["price"]))
    if best_bid <= best_ask:
        return Decimal("0")

    return best_bid - best_ask

