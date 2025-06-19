import asyncio
import logging
import os
from decimal import Decimal
from uuid import uuid4

from dotenv import load_dotenv
from paradex_py.paradex import Paradex
from paradex_py.common.order import Order, OrderSide, OrderType


def load_config():
    """Load configuration from environment or .env file."""
    load_dotenv()
    return {
        "env": os.getenv("PARADEX_ENV", "testnet"),
        "l1_address": os.getenv("PARADEX_L1_ADDRESS"),
        "l1_private_key": os.getenv("PARADEX_L1_PRIVATE_KEY"),
        "l2_private_key": os.getenv("PARADEX_L2_PRIVATE_KEY"),
    }


async def refresh_balance(paradex: Paradex) -> None:
    """Fetch account balances asynchronously."""
    try:
        balances = await asyncio.to_thread(paradex.api_client.fetch_balances)
        logging.info("Balances: %s", balances)
    except Exception as exc:
        logging.error("Failed to refresh balances: %s", exc)


async def place_orders(paradex: Paradex, order: Order) -> None:
    """Submit an order asynchronously."""
    try:
        result = await asyncio.to_thread(paradex.api_client.submit_order, order)
        logging.info("Order placed: %s", result)
    except Exception as exc:
        logging.error("Failed to place order: %s", exc)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = load_config()
    paradex = Paradex(
        env=cfg["env"],
        l1_address=cfg["l1_address"],
        l1_private_key=cfg["l1_private_key"],
        l2_private_key=cfg["l2_private_key"],
    )

    order = Order(
        market="ETH-USD-PERP",
        order_type=OrderType.LIMIT,
        order_side=OrderSide.BUY,
        size=Decimal("0.01"),
        limit_price=Decimal("3500"),
        client_id=str(uuid4()),
    )

    await refresh_balance(paradex)
    await place_orders(paradex, order)


if __name__ == "__main__":
    asyncio.run(main())
