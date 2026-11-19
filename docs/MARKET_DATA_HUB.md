# Market Data Hub

## Purpose

The Market Data Hub is the provider boundary for research, market intelligence,
forecasting and (later) optional signal workflows. It is not itself a signal
generator. Consumers receive normalized observations plus explicit provenance
and quality warnings.

## Initial adapters

The hub now includes three distinct public adapters:

- `BinanceUSDMPublicAdapter` for Binance USD-M linear perpetuals.
- `BinanceSpotPublicAdapter` for Binance spot.
- `CoinbaseExchangeSpotAdapter` for Coinbase Exchange spot.

`BinanceUSDMPublicAdapter` uses public Binance USD-M endpoints only:

- `/fapi/v1/klines` for linear perpetual OHLCV.
- `/fapi/v1/fundingRate` for funding event history.
- `/futures/data/openInterestHist` for aggregate open-interest history.

No API key, account endpoint, private position, or order endpoint is used. Binance spot and Coinbase spot use separate provider identities, endpoints and market-type metadata. Spot adapters expose OHLCV only and cannot be used for derivatives requests. The USD-M adapter remains limited to linear perpetual contracts.
Inverse futures, equities and local exchanges require distinct adapters
and canonical instrument metadata; they must not be silently mixed.

## Data contracts and quality

- OHLCV timestamps are UTC exchange candle-open timestamps.
- Funding timestamps represent exchange funding event times.
- Open-interest timestamps represent provider aggregate snapshot times.
- Retrieval time is recorded separately from source event time.
- Price, volume, timestamp and OHLC geometry are validated.
- Duplicate candle timestamps are deduplicated with a warning.
- Gaps are reported. No interpolation, forward-fill, synthetic candle or
  fabricated historical record is created.
- Provider, market type, endpoint, instrument and timestamp semantics remain
  attached to each response.
- The latest candle may still be forming. Downstream closed-bar logic must
  exclude it before indicator calculation, model training or evaluation.
- Different providers, quote currencies, contract types and OI units must not
  be combined without an explicit, tested conversion and alignment policy.

## Resilience

Adapters have bounded timeouts, bounded retries, exponential backoff and
Retry-After handling for HTTP 418/429. Repeated throttling raises a typed
exception. HTTP 5xx and transport failures fail closed after the retry budget.
The unit suite uses injected transports and does not require network access. The provider registry checks capabilities, so a spot-only adapter cannot be called for funding or open-interest history.

## Research integrity

Historical backtests and out-of-sample datasets must retain provider,
instrument, market type, timeframe and retrieval provenance. Live observations
must never silently overwrite historical research inputs. Forecast consumers
must use point-in-time data only and preserve an immutable evaluation boundary.

## Safety

All methods are read-only. The result is market data, not a recommendation.
The hub does not infer long/short positioning from OI, forecast funding payments,
calculate exchange liquidation prices, create signals, or submit orders.
