import numpy as np
import pandas as pd
import predictive_intelligence as pmi


def frame(n=700, seed=4):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0002, 0.008, n)
    c = 100*np.exp(np.cumsum(r))
    return pd.DataFrame({"open": c*(1-rng.normal(0, .001, n)), "high": c*1.01,
                         "low": c*.99, "close": c, "volume": rng.lognormal(4, .2, n)})


def test_pmi_is_finite_and_has_no_fake_probability():
    out = pmi.analyze(frame(), horizons=(1, 4, 12), paths=600)
    assert out["directional_probability"] is None
    assert set(out["scenario_forecasts"]) == {"1", "4", "12"}
    assert out["scenario_forecasts"]["4"]["price_quantiles"]["p10"] > 0


def test_regime_requires_history():
    try:
        pmi.classify_regime(np.arange(20)+100)
    except ValueError:
        return
    assert False, "expected insufficient history error"


def test_validation_metrics():
    out = pmi.validation_summary([0, 1, 1], [0.1, 0.8, 0.7], [0.1, 0.8, 0.7])
    assert out["n"] == 3 and out["brier"] >= 0


def test_pmi_verifies_timestamps_when_available_and_rejects_reordering():
    candles = frame()
    candles["time"] = np.arange(len(candles), dtype=float) * 3600
    result = pmi.analyze(candles, horizons=(1,), paths=100)
    assert result["input_order_status"] == "VERIFIED_STRICTLY_INCREASING"
    candles.loc[100, "time"] = candles.loc[99, "time"]
    try:
        pmi.analyze(candles, horizons=(1,), paths=100)
    except ValueError as exc:
        assert "strictly increasing" in str(exc)
    else:
        raise AssertionError("unordered timestamps must be rejected")


def test_pmi_discloses_unverifiable_order_when_timestamps_are_absent():
    result = pmi.analyze(frame(), horizons=(1,), paths=100)
    assert result["input_order_status"] == "UNVERIFIED_NO_TIMESTAMP"
