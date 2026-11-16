import numpy as np
import pandas as pd
import pytest
from research_rule_engine import compile_long_flat_rule


def test_rule_engine_emits_causal_long_flat_positions_and_exit():
    f=pd.DataFrame({'score':[0,.3,.4,.1,.5,.6], 'regime':['r']*6})
    spec={'entry':{'all':[{'feature':'score','op':'>=','value':.25}]},
          'exit':{'all':[{'feature':'score','op':'<','value':.2}]}}
    out=compile_long_flat_rule(f,spec)
    assert out.tolist()==[0,1,1,0,1,1]


def test_rule_engine_supports_crossovers_and_max_holding():
    f=pd.DataFrame({'score':[0,.1,.3,.4,.5,.6]})
    spec={'entry':{'all':[{'feature':'score','op':'crosses_above','value':.2}]},'max_holding_bars':2}
    assert compile_long_flat_rule(f,spec).tolist()==[0,0,1,1,0,0]


def test_rule_engine_rejects_unknown_features_and_eval_like_fields():
    f=pd.DataFrame({'score':[0,1,2]})
    with pytest.raises(ValueError,match='unknown research feature'):
        compile_long_flat_rule(f,{'entry':{'all':[{'feature':'__import__','op':'>','value':0}]}})
    with pytest.raises(ValueError,match='unsupported top-level'):
        compile_long_flat_rule(f,{'entry':{'all':[{'feature':'score','op':'>','value':0}]},'python':'x'})


def test_feature_prefix_is_invariant_to_future_candles():
    import app
    from research_rule_engine import build_feature_frame
    rng=np.random.default_rng(18); n=360; close=100*np.exp(np.cumsum(rng.normal(0,.004,n)))
    df=pd.DataFrame({'time':np.arange(n)*3600.,'open':close,'high':close*1.01,
                     'low':close*.99,'close':close,'volume':rng.uniform(10,100,n)})
    cut=250
    full=build_feature_frame(df,app.compute(df,'1h'))
    prefix=build_feature_frame(df.iloc[:cut].copy(),app.compute(df.iloc[:cut].copy(),'1h'))
    common=[c for c in prefix.columns if c in full.columns and pd.api.types.is_numeric_dtype(prefix[c])]
    np.testing.assert_allclose(full.loc[:cut-1,common].to_numpy(dtype=float),
                               prefix[common].to_numpy(dtype=float),rtol=1e-9,atol=1e-9,equal_nan=True)


def test_registered_hypothesis_runs_through_existing_oos_engine(monkeypatch,tmp_path):
    import app
    rng=np.random.default_rng(44); n=420; close=100*np.exp(np.cumsum(rng.normal(.0002,.004,n)))
    df=pd.DataFrame({'time':np.arange(n)*3600.,'open':close,'high':close*1.01,
                     'low':close*.99,'close':close,'volume':rng.uniform(20,200,n)})
    monkeypatch.setattr(app,'load_cached',lambda *a,**k:df)
    monkeypatch.setattr(app,'RESEARCH_REGISTRY_PATH',str(tmp_path/'registry.db'))
    rule={'entry':{'all':[{'feature':'score','op':'>=','value':0.1}]},
          'exit':{'any':[{'feature':'score','op':'<','value':0.0}]},'max_holding_bars':12}
    out=app.api_run_registered_hypothesis({'hypothesis_id':'TEST-RULE-001','name':'Test rule',
        'source':'csv','symbol':'BTCUSDT','tf':'1h','n':420,'n_splits':3,'warmup_bars':100,
        'rule_spec':rule})
    assert out['hypothesis_id']=='TEST-RULE-001'
    assert out['strategy_frozen'] is True and out['parameter_optimization'] is False
    assert out['deployment_status']=='RESEARCH_ONLY_NOT_CONNECTED_TO_LIVE_GATE'
    assert out['cost_stress_1_5x']['method'].startswith('exact chronological')
    assert out['research_registry']['experiments']==1
