# tradebot

## place_order.py

A simple CLI to submit orders to [Paradex](https://docs.paradex.trade/). The script reads configuration from environment variables (or a `.env` file) and sends a `LIMIT` or `MARKET` order via the SDK.

### Required environment

```
PARADEX_ENV=testnet           # or 'mainnet'
PARADEX_L1_ADDRESS=0x...
PARADEX_L1_PRIVATE_KEY=...
# or alternatively
PARADEX_L2_PRIVATE_KEY=...
```

### Example usage

```bash
python place_order.py ETH-USD-PERP BUY LIMIT 0.01 --price 3500
```

Use a valid market symbol (e.g. `ETH-USD-PERP`). You can retrieve the
available markets via `paradex_cli markets`.

Pass `--help` to see all options.
