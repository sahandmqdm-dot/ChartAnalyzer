"""Per-trade, auditable accounting view. No signal changes or causal claims."""
from __future__ import annotations
import math
from typing import Any, Dict, Iterable, List


def _finite(value: Any):
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_trade_forensics(trades: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize completed trade rows into explicit gross/net audit records."""
    out = []
    for index, trade in enumerate(trades or [], 1):
        if not isinstance(trade, dict):
            continue
        net = _finite(trade.get("net_ret"))
        gross_account = _finite(trade.get("gross_account_ret", trade.get("gross_ret")))
        gross_asset = _finite(trade.get("gross_ret"))
        mfe = _finite(trade.get("mfe"))
        mae = _finite(trade.get("mae"))
        drag = (gross_account - net) if gross_account is not None and net is not None else None
        result = "unverified" if net is None else "win" if net > 0 else "loss" if net < 0 else "breakeven"
        out.append({
            "trade_index": index, "side": str(trade.get("side", "unknown")),
            "entry_bar": trade.get("entry_bar"), "exit_bar": trade.get("exit_bar"),
            "entry_px": _finite(trade.get("entry_px")), "exit_px": _finite(trade.get("exit_px")),
            "gross_asset_return_pct": round(gross_asset * 100, 4) if gross_asset is not None else None,
            "gross_account_return_pct": round(gross_account * 100, 4) if gross_account is not None else None,
            "net_account_return_pct": round(net * 100, 4) if net is not None else None,
            "net_result": result,
            "cost_drag_account_pct": round(drag * 100, 4) if drag is not None else None,
            "mfe_pct": round(mfe * 100, 4) if mfe is not None else None,
            "mae_pct": round(mae * 100, 4) if mae is not None else None,
            "bars_held": trade.get("bars_held"), "exit_reason": str(trade.get("exit_reason", "unknown")),
            "entry_regime": trade.get("entry_regime"),
            "entry_signal_band": trade.get("entry_signal_band"),
        })
    return out


__all__ = ["build_trade_forensics"]
