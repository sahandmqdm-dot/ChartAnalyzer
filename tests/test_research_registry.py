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
