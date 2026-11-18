import pytest
from holdout_vault import HoldoutVault


def test_holdout_seal_is_one_way_and_access_is_recorded(tmp_path):
    v=HoldoutVault(tmp_path/'vault.json')
    assert v.status()['status']=='UNCONFIGURED'
    out=v.seal(dataset_id='D1',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=10,end_timestamp=20)
    assert out['status']=='SEALED' and out['locked']
    assert v.claim(dataset_id='D1',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=10,end_timestamp=20)['access_count']==1
    with pytest.raises(ValueError):
        v.seal(dataset_id='D2',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=20,end_timestamp=30)
    with pytest.raises(ValueError):
        v.claim(dataset_id='D2',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=10,end_timestamp=20)


def test_sealed_holdout_blocks_overlapping_ordinary_research_and_is_single_use(tmp_path):
    v=HoldoutVault(tmp_path/'vault.json')
    v.seal(dataset_id='D1',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=100,end_timestamp=200)
    assert v.overlaps(source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=150,end_timestamp=250)
    assert not v.overlaps(source='ramzinex',symbol='BTCIRR',timeframe='1h',start_timestamp=150,end_timestamp=250)
    v.claim(dataset_id='D1',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=100,end_timestamp=200)
    with pytest.raises(ValueError,match='already been opened'):
        v.claim(dataset_id='D1',config_hash='C1',source='ramzinex',symbol='USDTIRR',timeframe='1h',start_timestamp=100,end_timestamp=200)


def test_holdout_seal_rejects_oversized_identity_fields(tmp_path):
    v = HoldoutVault(tmp_path / "vault.json")
    with pytest.raises(ValueError, match="too long"):
        v.seal(dataset_id="D1", config_hash="C1", source="ramzinex",
               symbol="X" * 33, timeframe="1h",
               start_timestamp=10, end_timestamp=20)
