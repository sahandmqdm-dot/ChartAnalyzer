import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from execution_viability import analyze_execution_viability


def _trades(n=24, gross=0.002, net=-0.003, mfe=0.001):
    return [{"gross_account_ret": gross, "net_ret": net, "intrabar_mfe": mfe} for _ in range(n)]


def test_detects_costs_dominating_favorable_excursion():
    r = analyze_execution_viability(_trades())
    assert r["verdict"] == "COSTS_DOMINATE_FAVORABLE_EXCURSION"
    assert r["metrics"]["mfe_to_cost_ratio"] == pytest.approx(0.2)
    assert r["metrics"]["mean_net_return_pct"] == -0.3


def test_small_sample_is_not_promoted():
    r = analyze_execution_viability(_trades(3), min_samples=20)
    assert r["verdict"] == "INSUFFICIENT_SAMPLE"


def test_missing_or_invalid_records_are_excluded():
    r = analyze_execution_viability([{}, {"gross_account_ret": 0.1, "net_ret": 0.05}], min_samples=2)
    assert r["samples"] == 1
    assert r["excluded"] == 1


def test_mfe_is_not_fabricated_when_missing():
    r = analyze_execution_viability([{"gross_account_ret": 0.01, "net_ret": 0.005} for _ in range(25)])
    assert r["metrics"]["mean_intrabar_mfe_pct"] is None
    assert r["metrics"]["trades_mfe_exceeded_cost_pct"] is None


def test_requires_valid_minimum_sample():
    with pytest.raises(ValueError):
        analyze_execution_viability([], min_samples=1)
