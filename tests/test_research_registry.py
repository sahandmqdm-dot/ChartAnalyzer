import os
import stat

import pytest

from research_registry import ResearchRegistry


def test_registry_immutable_and_idempotent(tmp_path):
    r=ResearchRegistry(tmp_path/'registry.db')
    spec={'entry':{'adx':20},'exit':{'atr':1.5}}
    assert r.register('H1','ADX pullback',spec,family_id='f')['created']
    assert not r.register('H1','ADX pullback',spec,family_id='f')['created']
    with pytest.raises(ValueError):
        r.register('H1','ADX pullback',{'entry':{'adx':21}},family_id='f')
    a=r.record(experiment_id='E1',hypothesis_id='H1',dataset_id='D1',config_hash='C1',
      source='ramzinex',symbol='USDTIRR',timeframe='1h',metrics={'trades':10},p_value=.02)
    b=r.record(experiment_id='E1',hypothesis_id='H1',dataset_id='D1',config_hash='C1',
      source='ramzinex',symbol='USDTIRR',timeframe='1h',metrics={'trades':10},p_value=.02)
    assert a['created'] and not b['created']
    assert r.apply_fdr('f')['tests_counted']==1
    assert r.summary()['experiments']==1


def test_registry_rejects_oversized_hypothesis_specification(tmp_path):
    r = ResearchRegistry(tmp_path / "registry.db")
    with pytest.raises(ValueError, match="64 KiB limit"):
        r.register("H-LARGE", "large", {"payload": "x" * (65 * 1024)})


def test_registry_rejects_oversized_experiment_metrics(tmp_path):
    r = ResearchRegistry(tmp_path / "registry.db")
    r.register("H1", "valid", {"entry": {"adx": 20}})
    with pytest.raises(ValueError, match="256 KiB limit"):
        r.record(experiment_id="E-LARGE", hypothesis_id="H1", dataset_id="D1",
                 config_hash="C1", source="ramzinex", symbol="USDTIRR",
                 timeframe="1h", metrics={"payload": "x" * (257 * 1024)},
                 p_value=None)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode semantics")
def test_registry_database_is_owner_only(tmp_path):
    path = tmp_path / "private-registry.db"
    ResearchRegistry(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_registry_refuses_symlink_database_path(tmp_path):
    target = tmp_path / "target.db"
    target.write_bytes(b"not a database")
    link = tmp_path / "linked.db"
    link.symlink_to(target)
    with pytest.raises(OSError, match="symbolic link"):
        ResearchRegistry(link)



def test_registry_rejects_reuse_of_hypothesis_id_with_changed_name_or_family(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.db")
    spec = {"entry": {"rsi": 30}}
    registry.register("H-IMMUTABLE", "RSI rebound", spec, family_id="family-a")
    with pytest.raises(ValueError, match="immutable"):
        registry.register("H-IMMUTABLE", "Different name", spec, family_id="family-a")
    with pytest.raises(ValueError, match="immutable"):
        registry.register("H-IMMUTABLE", "RSI rebound", spec, family_id="family-b")


def test_registry_rejects_experiment_id_reuse_with_changed_evidence(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.db")
    registry.register("H1", "hypothesis", {"entry": "frozen"})
    base = {
        "experiment_id": "E-IMMUTABLE", "hypothesis_id": "H1",
        "dataset_id": "D1", "config_hash": "C1", "source": "binance",
        "symbol": "BTCUSDT", "timeframe": "1m", "metrics": {"trades": 100},
        "p_value": 0.02, "run_id": "run-1", "sample_start": 1, "sample_end": 2,
    }
    assert registry.record(**base)["created"] is True
    assert registry.record(**base)["created"] is False
    changed = {**base, "metrics": {"trades": 101}}
    with pytest.raises(ValueError, match="Experiment ID is immutable"):
        registry.record(**changed)


@pytest.mark.parametrize("metrics", [[], "not-an-object", None])
def test_registry_requires_metrics_object(tmp_path, metrics):
    registry = ResearchRegistry(tmp_path / "registry.db")
    registry.register("H1", "hypothesis", {"entry": "frozen"})
    with pytest.raises(TypeError, match="metrics"):
        registry.record(
            experiment_id="E1", hypothesis_id="H1", dataset_id="D1",
            config_hash="C1", source="binance", symbol="BTCUSDT",
            timeframe="1m", metrics=metrics, p_value=None,
        )


def test_registry_rejects_invalid_sample_ranges_and_nonfinite_pvalues(tmp_path):
    registry = ResearchRegistry(tmp_path / "registry.db")
    registry.register("H1", "hypothesis", {"entry": "frozen"})
    args = {
        "experiment_id": "E1", "hypothesis_id": "H1", "dataset_id": "D1",
        "config_hash": "C1", "source": "binance", "symbol": "BTCUSDT",
        "timeframe": "1m", "metrics": {}, "p_value": None,
    }
    with pytest.raises(ValueError, match="sample_end"):
        registry.record(**args, sample_start=10, sample_end=9)
    with pytest.raises(ValueError, match="p_value"):
        registry.record(**{**args, "p_value": float("nan")})
