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
PARADEX_LEVERAGE=30            # optional leverage for arbitrage_bot
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Example usage

```bash
python place_order.py ETH-USD-PERP BUY LIMIT 0.01 --price 3500
```

Use a valid market symbol (e.g. `ETH-USD-PERP`). You can retrieve the
available markets via `paradex_cli markets`.

Pass `--help` to see all options.

### Troubleshooting

Set the `PARADEX_LOG_LEVEL` environment variable to `DEBUG` to see detailed
log output from both the bots and the SDK. This can help diagnose connection
or order placement issues.
