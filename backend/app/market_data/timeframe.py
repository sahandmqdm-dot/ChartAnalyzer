from enum import StrEnum


class Timeframe(StrEnum):
    """Exchange-independent candle intervals using canonical notation."""

    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H8 = "8h"
    H12 = "12h"
    D1 = "1d"
    D3 = "3d"
    W1 = "1w"
    MONTH_1 = "1M"

    @property
    def seconds(self) -> int | None:
        """Fixed duration in seconds; calendar months intentionally return None."""
        fixed = {
            Timeframe.M1: 60,
            Timeframe.M3: 180,
            Timeframe.M5: 300,
            Timeframe.M15: 900,
            Timeframe.M30: 1800,
            Timeframe.H1: 3600,
            Timeframe.H2: 7200,
            Timeframe.H4: 14400,
            Timeframe.H6: 21600,
            Timeframe.H8: 28800,
            Timeframe.H12: 43200,
            Timeframe.D1: 86400,
            Timeframe.D3: 259200,
            Timeframe.W1: 604800,
        }
        return fixed.get(self)
