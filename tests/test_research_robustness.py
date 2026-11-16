from research_robustness import parameter_neighborhood_stability,cross_symbol_stability,regime_stability


def test_parameter_stability_uses_neighborhood_not_best_point_only():
    rows=[{'net_expectancy_pct':x} for x in [.2,.15,.1,.08,-.01]]
    out=parameter_neighborhood_stability(rows)
    assert out['status']=='PASS' and out['positive_ratio']==.8


def test_cross_symbol_requires_minimum_independent_symbols():
    assert cross_symbol_stability([{'symbol':'A','net_expectancy_pct':.1}])['status']=='INSUFFICIENT_EVIDENCE'
    out=cross_symbol_stability([{'symbol':s,'net_expectancy_pct':.1} for s in ['A','B','C']])
    assert out['status']=='PASS'


def test_regime_stability_refuses_tiny_cohorts():
    out=regime_stability([{'regime':'trend','trades':10,'net_expectancy_pct':.2}])
    assert out['status']=='INSUFFICIENT_EVIDENCE'
