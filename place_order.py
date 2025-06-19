import argparse
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
    config = {
        "env": os.getenv("PARADEX_ENV", "testnet"),
        "l1_address": os.getenv("PARADEX_L1_ADDRESS"),
        "l1_private_key": os.getenv("PARADEX_L1_PRIVATE_KEY"),
        "l2_private_key": os.getenv("PARADEX_L2_PRIVATE_KEY"),
    }
    missing = [k for k in ["l1_address"] if not config[k]]
    if missing:
        missing_str = ", ".join(missing)
        raise SystemExit(
            f"Missing required config variables: {missing_str}"
        )
    if not config["l1_private_key"] and not config["l2_private_key"]:
        raise SystemExit(
            "Provide PARADEX_L1_PRIVATE_KEY or PARADEX_L2_PRIVATE_KEY"
        )
    return config


def parse_args():
    parser = argparse.ArgumentParser(description="Place order on Paradex")
    parser.add_argument("market", help="Market symbol, e.g. ETH-USD")
    parser.add_argument("side", choices=["BUY", "SELL"], help="Order side")
    parser.add_argument("type", choices=["LIMIT", "MARKET"], help="Order type")
    parser.add_argument("size", type=Decimal, help="Order size")
    parser.add_argument("--price", type=Decimal, help="Limit price")
    parser.add_argument(
        "--client-id",
        default=str(uuid4()),
        help="Client order id",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    cfg = load_config()

    if args.type == "LIMIT" and args.price is None:
        raise SystemExit("Limit orders require --price")

    paradex = Paradex(
        env=cfg["env"],
        l1_address=cfg["l1_address"],
        l1_private_key=cfg["l1_private_key"],
        l2_private_key=cfg["l2_private_key"],
    )

    order = Order(
        market=args.market,
        order_type=OrderType[args.type.title()],
        order_side=OrderSide[args.side.title()],
        size=args.size,
        limit_price=args.price or Decimal(0),
        client_id=args.client_id,
    )

    try:
        result = paradex.api_client.submit_order(order)
        logging.info("Order placed successfully: %s", result)
    except Exception as exc:
        logging.error("Failed to place order: %s", exc)


if __name__ == "__main__":
    main()
