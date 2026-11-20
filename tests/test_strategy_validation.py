import json

from version import APP_VERSION

from strategy_validation import (
    build_evidence_record,
    deployment_status,
    evaluate_evidence,
    save_evidence,
)


def passing_result():
    return {
        "entry_edge_research": {"overall": {"bootstrap_ci_95_pct": [0.08, 0.42]}},
        "folds": [
            {"strategy_pct": 1.2}, {"strategy_pct": 0.4},
            {"strategy_pct": 0.8}, {"strategy_pct": -0.1},
            {"strategy_pct": 0.3},
        ],
        "data_integrity": {"valid": True, "gap_count": 0},
        "provenance": {"run_id": "abc123", "dataset_id": "data123", "last_timestamp": 1_700_000_000},
        "research_cohort_integrity": {"status": "PASS"},
        "strategy_frozen": True,
        "parameter_optimization": False,
        "trades": 240,
        "net_expectancy_pct": 0.15,
        "net_profit_factor": 1.24,
        "max_dd_pct": -8.0,
        "research_inference": {"p_value": 0.01, "bootstrap_ci_95_pct": [0.08, 0.42]},
        "adjusted_p_value": 0.02,
        "cost_stress_1_5x_positive": True,
        "cost_stress_1_5x": {"net_expectancy_pct": 0.04, "positive_expectancy": True},
    }


def test_positive_oos_record_can_pass_only_with_all_evidence_requirements():
    record = build_evidence_record(
        passing_result(), source="csv", symbol="BTCUSDT", timeframe="1h",
        config_hash="f" * 64, created_at=1_700_000_000,
    )
    decision = evaluate_evidence(record, now=1_700_000_001)
    assert decision["eligible"] is True
    assert decision["status"] == "ELIGIBLE"
    assert decision["oos_trades"] == 240
    assert decision["positive_folds"] == 4


def test_negative_oos_performance_blocks_deployment_even_if_signal_score_is_strong():
    result = passing_result()
    result["net_expectancy_pct"] = -0.58
    result["net_profit_factor"] = 0.119
    result["entry_edge_research"]["overall"]["bootstrap_ci_95_pct"] = [-0.75, -0.39]
    result["research_inference"]["bootstrap_ci_95_pct"] = [-0.75, -0.39]
    record = build_evidence_record(
        result, source="ramzinex", symbol="USDTIRR", timeframe="1h",
        config_hash="e" * 64, created_at=1_700_000_000,
    )
    decision = evaluate_evidence(record, now=1_700_000_001)
    assert decision["eligible"] is False
    assert any("expectancy" in x.lower() for x in decision["reasons"])
    assert any("profit factor" in x.lower() for x in decision["reasons"])
    assert any("confidence" in x.lower() or "CI" in x for x in decision["reasons"])


def test_evidence_expires_and_gap_or_cohort_errors_fail_closed():
    result = passing_result()
    result["data_integrity"]["gap_count"] = 3
    result["research_cohort_integrity"]["status"] = "MISMATCH"
    record = build_evidence_record(
        result, source="csv", symbol="BTCUSDT", timeframe="1h",
        config_hash="a" * 64, created_at=1_700_000_000,
    )
    decision = evaluate_evidence(record, {"validation_max_age_hours": 1}, now=1_700_010_000)
    assert decision["status"] == "BLOCKED"
    assert any("expired" in x.lower() for x in decision["reasons"])
    assert any("gaps" in x.lower() for x in decision["reasons"])
    assert any("cohort" in x.lower() for x in decision["reasons"])


def test_evidence_store_is_keyed_by_exact_config_and_writes_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("CHARTANALYZER_EVIDENCE_SIGNING_KEY", "test-signing-key-" + "x" * 40)
    record = build_evidence_record(
        passing_result(), source="csv", symbol="BTCUSDT", timeframe="1h",
        config_hash="b" * 64, created_at=1_700_000_000,
    )
    path = tmp_path / "research_evidence.json"
    save_evidence(path, record)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert list(stored) == [record["key"]]
    yes = deployment_status(path, source="csv", symbol="BTCUSDT", timeframe="1h",
                            config_hash="b" * 64, now=1_700_000_001)
    no = deployment_status(path, source="csv", symbol="BTCUSDT", timeframe="1h",
                           config_hash="c" * 64, now=1_700_000_001)
    assert yes["eligible"] is True
    assert no["status"] == "MISSING"


def test_live_gate_still_requires_matching_oos_validation_and_existing_ev_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("CHARTANALYZER_EVIDENCE_SIGNING_KEY", "test-signing-key-" + "x" * 40)
    import app
    from research_provenance import config_fingerprint

    C = app._merge(app.DEFAULTS, {})
    symbol, timeframe, source = "BTCUSDT", "1h", "csv"
    config_hash = config_fingerprint(app._research_execution_config(timeframe, symbol, None, C))
    record = build_evidence_record(
        passing_result(), source=source, symbol=symbol, timeframe=timeframe,
        config_hash=config_hash,
    )
    store = tmp_path / "evidence.json"
    save_evidence(store, record)
    monkeypatch.setattr(app, "VALIDATION_EVIDENCE_PATH", str(store))

    summary = {"tf": timeframe, "edge": {"aggregate": {"t_eff": 3.0, "n": 100}}}
    plan = {"stop": 95.0, "t1": 115.0}
    plan_hit = {"t1_first_pct": 80.0, "stop_first_pct": 10.0}
    allowed = app.make_gate(summary | {"plan_hit": plan_hit}, plan, 100.0, C,
                            symbol=symbol, source=source)
    assert allowed["deployment_validation"]["eligible"] is True
    assert allowed["state"] == "ALLOWED"

    # A different threshold/configuration cannot reuse the previous evidence.
    C_changed = app._merge(C, {"verdict_threshold": 0.25})
    blocked = app.make_gate(summary | {"plan_hit": plan_hit}, plan, 100.0, C_changed,
                            symbol=symbol, source=source)
    assert blocked["state"] == "NO_TRADE"
    assert blocked["deployment_validation"]["status"] == "MISSING"


def test_canonical_research_fingerprint_ignores_untrusted_report_metrics_but_tracks_wf_settings():
    import app
    from research_provenance import config_fingerprint
    C=app._merge(app.DEFAULTS,{})
    baseline=app._research_execution_config('1h','USDTIRR',None,C)
    report_only=app._research_execution_config('1h','USDTIRR',{
        'fee_per_side':0.049,'spread_slippage_rt':0.04,'stop_atr_mult':9.0,
        'market_type':'futures','leverage':99,'trades':999,'strategy_pct':42.0
    },C)
    assert config_fingerprint(baseline)==config_fingerprint(report_only)

    # A request-level evaluation override must not reuse evidence from the
    # configured/default split, warm-up or embargo settings.
    for override in (
        {'configured_n_splits':7},
        {'configured_warmup_bars':240,'effective_warmup_bars':240},
        {'configured_gap_bars':3},
    ):
        changed=app._research_execution_config('1h','USDTIRR',override,C)
        assert config_fingerprint(baseline)!=config_fingerprint(changed)

    # The ordinary live gate and an OOS run using the exact configured values
    # must still compute the same fingerprint.
    configured=C['walk_forward']
    same=app._research_execution_config('1h','USDTIRR',{
        'configured_n_splits':configured['n_splits'],
        'configured_warmup_bars':configured['warmup_bars'],
        'configured_gap_bars':configured['gap_bars'],
        'effective_warmup_bars':max(configured['warmup_bars'],app.required_indicator_warmup('1h',C)),
    },C)
    assert config_fingerprint(baseline)==config_fingerprint(same)


def test_research_lab_routes_are_registered():
    import app
    assert ('GET','/api/research-lab') in app.app.routes
    assert ('GET','/api/research-lab/permutation') in app.app.routes
    assert ('GET','/api/research-lab/random-entry') in app.app.routes
    assert ('GET','/api/research-lab/fingerprint') in app.app.routes
    assert ('GET','/api/research-lab/split-preview') in app.app.routes
    assert ('POST','/api/research-lab/robustness') in app.app.routes
    assert ('POST','/api/research-lab/holdout/evaluate') in app.app.routes


def test_user_config_cannot_weaken_mandatory_research_gate_floors():
    record=build_evidence_record(
        passing_result(),source='ramzinex',symbol='USDTIRR',timeframe='1h',
        config_hash='d'*64,created_at=1_700_000_000)
    # Deliberately attempt to restore V7.1's permissive thresholds.
    decision=evaluate_evidence(record,{
        'min_oos_trades':30,'min_oos_net_profit_factor':1.01,
        'max_oos_drawdown_pct':50,'max_adjusted_p_value':.5
    },now=1_700_000_001)
    assert decision['policy']['min_oos_trades']==200
    assert decision['policy']['min_oos_net_profit_factor']==1.2
    assert decision['policy']['max_oos_drawdown_pct']==15
    assert decision['policy']['max_adjusted_p_value']==.05
    assert decision['policy']['min_validation_folds'] >= 5
    assert decision['policy']['validation_max_age_hours'] <= 24
    assert decision['policy']['min_oos_net_expectancy_pct'] >= 0
    assert decision['policy']['require_positive_cost_stress_1_5x'] is True
    assert decision['policy']['require_positive_oos_ci'] is True
    assert decision['policy']['require_gap_free_data'] is True


def test_research_lab_dashboard_api_returns_registry_and_holdout_status(monkeypatch,tmp_path):
    import app
    monkeypatch.setattr(app,'RESEARCH_REGISTRY_PATH',str(tmp_path/'registry.db'))
    monkeypatch.setattr(app,'HOLDOUT_VAULT_PATH',str(tmp_path/'vault.json'))
    out=app.api_research_lab()
    assert out['engine']==f"ChartAnalyzer Quant Research Engine {APP_VERSION}"
    assert out['registry']['experiments']==0
    assert out['holdout']['status']=='UNCONFIGURED'
    assert out['controls']['fdr'] is True


def test_split_preview_and_robustness_api_are_wired():
    import app
    split=app.api_research_split_preview(n=1000)
    assert split['split']['train']==(0,600)
    robust=app.api_research_robustness({
        'parameter_runs':[{'net_expectancy_pct':.1},{'net_expectancy_pct':.2}],
        'symbol_runs':[{'symbol':'A','net_expectancy_pct':.1}],
        'regime_runs':[{'regime':'trend','trades':5,'net_expectancy_pct':.2}]
    })
    assert robust['parameter_stability']['status']=='PASS'
    assert robust['cross_symbol_stability']['status']=='INSUFFICIENT_EVIDENCE'


def test_nonpositive_1_5x_cost_stress_blocks_deployment():
    result=passing_result()
    result['cost_stress_1_5x_positive']=False
    result['cost_stress_1_5x']={'net_expectancy_pct':-0.02,'positive_expectancy':False}
    record=build_evidence_record(result,source='ramzinex',symbol='USDTIRR',timeframe='1h',
                                config_hash='a'*64,created_at=1_700_000_000)
    decision=evaluate_evidence(record,now=1_700_000_001)
    assert not decision['eligible']
    assert any('1.5x' in x for x in decision['reasons'])



def test_signed_evidence_detects_post_save_metric_tampering(tmp_path, monkeypatch):
    from strategy_validation import load_evidence
    monkeypatch.setenv("CHARTANALYZER_EVIDENCE_SIGNING_KEY", "tamper-test-key-" + "z" * 40)
    record = build_evidence_record(
        passing_result(), source="csv", symbol="BTCUSDT", timeframe="1h",
        config_hash="c" * 64, created_at=1_700_000_000,
    )
    path = tmp_path / "signed.json"
    save_evidence(path, record)
    assert load_evidence(path, source="csv", symbol="BTCUSDT", timeframe="1h",
                         config_hash="c" * 64) is not None

    stored = json.loads(path.read_text(encoding="utf-8"))
    stored[record["key"]]["net_profit_factor"] = 99.0
    path.write_text(json.dumps(stored), encoding="utf-8")
    assert load_evidence(path, source="csv", symbol="BTCUSDT", timeframe="1h",
                         config_hash="c" * 64) is None


def test_unsigned_evidence_is_not_deployable(tmp_path, monkeypatch):
    from strategy_validation import load_evidence
    monkeypatch.delenv("CHARTANALYZER_EVIDENCE_SIGNING_KEY", raising=False)
    record = build_evidence_record(
        passing_result(), source="csv", symbol="BTCUSDT", timeframe="1h",
        config_hash="d" * 64, created_at=1_700_000_000,
    )
    path = tmp_path / "unsigned.json"
    save_evidence(path, record)
    assert load_evidence(path, source="csv", symbol="BTCUSDT", timeframe="1h",
                         config_hash="d" * 64) is None


def test_production_gate_cannot_be_disabled_by_user_config(monkeypatch, tmp_path):
    import app
    monkeypatch.delenv("CHARTANALYZER_EVIDENCE_SIGNING_KEY", raising=False)
    monkeypatch.setattr(app, "VALIDATION_EVIDENCE_PATH", str(tmp_path / "missing.json"))
    C = app._merge(app.DEFAULTS, {"gate": {"require_oos_validation": False}})
    summary = {"tf": "1h", "edge": {"aggregate": {"t_eff": 3.0, "n": 100}},
               "plan_hit": {"t1_first_pct": 80.0, "stop_first_pct": 10.0}}
    decision = app.make_gate(summary, {"stop": 95.0, "t1": 115.0}, 100.0, C,
                             symbol="BTCUSDT", source="csv")
    assert decision["state"] == "NO_TRADE"
    assert decision["deployment_validation"]["eligible"] is False
