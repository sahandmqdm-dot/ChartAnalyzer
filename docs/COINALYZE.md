# Coinalyze futures data integration

ChartAnalyzer can read current futures Open Interest and Funding Rate snapshots from Coinalyze independently of the configured exchange-native Futures provider.

## Configuration

Set the key only in the local runtime environment or an untracked `.env` file:

```dotenv
COINALYZE_API_KEY=your-key
```

The repository ignores `.env` files. Never commit the real key, put it in frontend configuration, or include it in logs or support output. The setting is a Pydantic `SecretStr` and is not returned by API responses.

## Endpoint

Authenticated endpoint:

```http
GET /api/v1/derivatives/coinalyze/snapshot?symbols=BTCUSDT_PERP.A,BTCUSDT.6,BTC.H
```

- Accepts 1–20 Coinalyze futures market identifiers per request.
- Returns current Open Interest and Funding Rate in their raw provider units.
- Returns each series' provider update time converted from Unix milliseconds to UTC.
- A missing row for one metric is represented as `null`; zero is preserved as a real numeric value.
- Uses a 10-second process-local TTL/LRU cache and single-flight request coalescing.
- Uses read-only HTTPS GET requests with a bounded timeout and redirects disabled.
- Maps provider authentication and rate-limit failures to sanitized API errors.

The endpoint deliberately does not convert Open Interest into a common quote-currency notional. Coinalyze symbols can represent contracts with different multipliers, settlement assets, and quote conventions. Consumers must use market metadata before comparing or aggregating raw Open Interest across venues. Funding Rate is also returned as reported; it is not annualized or normalized to a common settlement interval.

## Current scope and next increments

This first integration slice covers current Open Interest and Funding Rate only. It does not claim support yet for historical OI/funding, liquidation history, long/short ratio, or OHLCV. Those require separate endpoint-specific adapters, pagination/range controls, and tests before being exposed. The Coinalyze source is independent of Binance/Bybit exchange-native adapters and does not replace mark-price, index-price, order-book, or WebSocket feeds.