import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API_BASE = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

type Candle = {
  timestamp: string;
  close_time: string | null;
  is_closed: boolean;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
};
type CandleResponse = { provider: string; symbol: string; interval: string; count: number; candles: Candle[] };

type EquityPoint = { timestamp: string; equity: string; drawdown_pct: string };
type FuturesOverview = {
  provider: string;
  market_type: string;
  symbol: string;
  mark_price: string;
  index_price: string;
  funding_rate: string;
  open_interest: string;
  open_interest_value: string;
  next_funding_time: string | null;
  observed_at: string;
};
type FuturesSeriesPoint = { timestamp: string; value: string; secondary_value: string | null };
type FuturesHistory = {
  provider: string; market_type: string; symbol: string; period: string; observed_at: string;
  funding_rate: FuturesSeriesPoint[]; open_interest: FuturesSeriesPoint[]; long_short_ratio: FuturesSeriesPoint[];
};

type FuturesSignal = { code: string; label: string; direction: string; severity: string; explanation: string };
type FuturesAnalysis = {
  symbol: string; period: string; price_change_pct: string | null; open_interest_change_pct: string | null;
  latest_funding_rate: string | null; latest_long_short_ratio: string | null; positioning: string;
  funding_bias: string; squeeze_risk: string; confidence: string; signals: FuturesSignal[];
  caveats: string[]; observed_at: string;
};



type MarketStructureSwing = { kind: 'high' | 'low'; timestamp: string; price: string };
type MarketStructureEvent = { code: string; direction: string; timestamp: string; level: string; explanation: string };
type FairValueGap = { direction: string; start_time: string; end_time: string; lower: string; upper: string; midpoint: string; status: string; mitigation_time: string | null; invalidation_time: string | null };
type OrderBlock = { direction: string; start_time: string; break_time: string; lower: string; upper: string; source_swing_level: string; status: string; mitigation_time: string | null; invalidation_time: string | null };
type MarketStructure = {
  symbol: string; period: string; provider: string; trend: string; latest_close: string | null;
  swings: MarketStructureSwing[]; events: MarketStructureEvent[]; fair_value_gaps: FairValueGap[]; order_blocks: OrderBlock[];
  liquidity_levels: string[]; closed_candles: number; pivot_window: number; observed_at: string; caveats: string[];
};

type ConfluenceTimeframe = {
  period: string; bias: string; trend: string; latest_close: string | null; closed_candles: number;
  latest_event: string | null; active_bullish_fvgs: number; active_bearish_fvgs: number;
  active_bullish_order_blocks: number; active_bearish_order_blocks: number; evidence: string[]; caveats: string[];
};
type MultiTimeframeConfluence = {
  symbol: string; provider: string; alignment: string; dominant_bias: string;
  directional_timeframes: number; bullish_timeframes: number; bearish_timeframes: number;
  neutral_timeframes: number; timeframes: ConfluenceTimeframe[]; observed_at: string;
  caveats: string[]; unavailable_timeframes: string[];
};

type LiveRiskAssessment = {
  provider: string;
  symbol: string;
  mark_price: string;
  mark_price_observed_at: string;
  risk_tiers_source: string;
  risk_tiers_observed_at: string;
  freshness_seconds: number;
  assessment_status: string;
  estimate: RiskEstimate & {
    liquidation_price_estimate: string;
    distance_to_liquidation_from_mark: string | null;
    distance_to_liquidation_from_mark_percent: string | null;
    bankruptcy_price_estimate: string;
    liquidation_tier: number;
  };
  alert: {
    level: 'normal' | 'warning' | 'critical' | 'liquidation_near';
    title: string;
    message: string;
    distance_percent: string;
    triggered: boolean;
  };
  thresholds: Record<string, string>;
};
type RiskTier = {
  tier: number; notional_floor: string; notional_cap: string | null;
  maintenance_margin_rate: string; maintenance_margin_deduction: string; max_leverage?: string | null;
};
type RiskEstimate = {
  symbol: string; side: 'long' | 'short'; margin_mode: string; entry_price: string;
  quantity: string; leverage: string; entry_notional: string; initial_margin_estimate: string;
  extra_margin: string; liquidation_price_estimate: string; liquidation_tier: number;
  maintenance_margin_rate: string; maintenance_margin_estimate: string; estimated_close_fee: string;
  distance_to_liquidation_percent: string; status: string; assumptions: string[];
};
type LiquidationEvent = {
  event_id: string; provider: string; symbol: string; event_time: string;
  liquidation_side: 'long' | 'short'; order_side: 'BUY' | 'SELL';
  quantity: string; price: string; notional: string; price_kind: string;
};
type LiquidationSnapshot = {
  status: { provider: string; symbol: string; state: string; event_count: number;
    last_event_time: string | null; last_error: string | null;
    persistence_state?: string; persistence_error?: string | null };
  events: LiquidationEvent[]; retention: string; storage_status?: string;
};
type LiquidationHeatmapCell = {
  time_bucket: string; price_bin: number; price_from: string; price_to: string;
  events: number; long_events: number; short_events: number;
  long_notional: string; short_notional: string; total_notional: string;
};
type LiquidationCluster = {
  price_from: string; price_to: string; mid_price: string; price_bins: number;
  events: number; long_events: number; short_events: number;
  long_notional: string; short_notional: string; total_notional: string;
  strength_pct: string; dominant_side: 'long' | 'short' | 'balanced'; last_event_time: string;
};
type MarkPriceCandle = {
  timestamp: string; close_time: string; is_closed: boolean;
  open: string; high: string; low: string; close: string;
};
type MarkPriceHistory = {
  provider: string; market_type: string; symbol: string; period: string;
  candles: MarkPriceCandle[]; observed_at: string;
};
type LiquidationAlert = { id: string; createdAt: string; kind: 'proximity' | 'concentration'; message: string };
type LiquidationClusters = {
  symbol: string; window: string; provider_filter: string; side_filter: string;
  min_strength_pct: number; min_events_per_band: number; total_events: number;
  truncated: boolean; price_min: string | null; price_max: string | null;
  price_step: string | null; total_notional: string; clusters: LiquidationCluster[];
  method: string;
  market_context: {
    status: string; provider?: string; funding_rate?: string | null;
    funding_timestamp?: string | null; open_interest?: string | null;
    open_interest_change_pct?: string | null; open_interest_timestamp?: string | null;
    long_short_ratio?: string | null; observed_at?: string; note: string;
  };
};
type LiquidationHeatmap = {
  symbol: string; window: string; bucket_minutes: number; price_bins: number;
  provider_filter: string; side_filter: string; start_time: string; end_time: string;
  providers: string[]; total_events: number; total_notional: string;
  price_min: string | null; price_max: string | null; price_step: string | null;
  truncated: boolean; cells: LiquidationHeatmapCell[]; data_semantics: string;
};

type BacktestTrade = {
  entry_time: string;
  exit_time: string;
  entry_price: string;
  exit_price: string;
  pnl: string;
  return_pct: string;
};
type BacktestResponse = {
  provider: string;
  symbol: string;
  interval: string;
  candles_used: number;
  result: {
    initial_equity: string;
    final_equity: string;
    net_return_pct: string;
    max_drawdown_pct: string;
    trade_count: number;
    winning_trades: number;
    losing_trades: number;
    win_rate_pct: string;
    profit_factor: string | null;
    expectancy: string;
    average_win: string;
    average_loss: string;
    exposure_pct: string;
    trades: BacktestTrade[];
    equity_curve: EquityPoint[];
  };
};

function equityCurvePolyline(points: EquityPoint[]): string {
  if (!points.length) return '';
  const values = points.map((point) => Number(point.equity));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = 8 + (index / Math.max(values.length - 1, 1)) * 784;
    const y = 172 - ((value - min) / span) * 150;
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');
}

function seriesPolyline(points: FuturesSeriesPoint[]): string {
  if (!points.length) return '';
  const values = points.map((point) => Number(point.value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = 8 + (index / Math.max(values.length - 1, 1)) * 784;
    const y = 172 - ((value - min) / span) * 150;
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');
}

function localDateTimeValue(date: Date): string {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function cookieValue(name: string): string {
  const prefix = `${name}=`;
  const item = document.cookie.split('; ').find((part) => part.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : '';
}

function App() {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [authenticated, setAuthenticated] = useState(false);
  const [csrf, setCsrf] = useState('');
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [interval, setInterval] = useState('1h');
  const [limit, setLimit] = useState('200');
  const [closedOnly, setClosedOnly] = useState(true);
  const [data, setData] = useState<CandleResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<'checking' | 'online' | 'offline'>('checking');
  const [error, setError] = useState('');
  const [backtestStart, setBacktestStart] = useState(() => localDateTimeValue(new Date(Date.now() - 30 * 86400000)));
  const [backtestEnd, setBacktestEnd] = useState(() => localDateTimeValue(new Date()));
  const [fastPeriod, setFastPeriod] = useState('10');
  const [slowPeriod, setSlowPeriod] = useState('30');
  const [backtestResult, setBacktestResult] = useState<BacktestResponse | null>(null);
  const [backtestBusy, setBacktestBusy] = useState(false);
  const [backtestError, setBacktestError] = useState('');
  const [futuresData, setFuturesData] = useState<FuturesOverview | null>(null);
  const [futuresBusy, setFuturesBusy] = useState(false);
  const [futuresError, setFuturesError] = useState('');
  const [futuresHistory, setFuturesHistory] = useState<FuturesHistory | null>(null);
  const [historyPeriod, setHistoryPeriod] = useState('1h');
  const [historyLimit, setHistoryLimit] = useState('100');
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [futuresAnalysis, setFuturesAnalysis] = useState<FuturesAnalysis | null>(null);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [analysisError, setAnalysisError] = useState('');
  const [marketStructurePeriod, setMarketStructurePeriod] = useState('1h');
  const [pivotWindow, setPivotWindow] = useState('3');
  const [marketStructure, setMarketStructure] = useState<MarketStructure | null>(null);
  const [marketStructureBusy, setMarketStructureBusy] = useState(false);
  const [marketStructureError, setMarketStructureError] = useState('');
  const [confluence, setConfluence] = useState<MultiTimeframeConfluence | null>(null);
  const [confluenceBusy, setConfluenceBusy] = useState(false);
  const [confluenceError, setConfluenceError] = useState('');

  const [riskSide, setRiskSide] = useState<'long' | 'short'>('long');
  const [riskEntry, setRiskEntry] = useState('95000');
  const [riskQuantity, setRiskQuantity] = useState('0.01');
  const [riskLeverage, setRiskLeverage] = useState('10');
  const [riskExtraMargin, setRiskExtraMargin] = useState('0');
  const [riskFee, setRiskFee] = useState('0.0005');
  const [riskTiersJson, setRiskTiersJson] = useState('');
  const [riskBusy, setRiskBusy] = useState(false);
  const [riskError, setRiskError] = useState('');
  const [riskResult, setRiskResult] = useState<RiskEstimate | null>(null);
  const [liveRiskAssessment, setLiveRiskAssessment] = useState<LiveRiskAssessment | null>(null);
  const [liveRiskBusy, setLiveRiskBusy] = useState(false);
  const [liveRiskError, setLiveRiskError] = useState('');
  const [liveRiskMonitoring, setLiveRiskMonitoring] = useState(false);
  const [liquidationLive, setLiquidationLive] = useState(false);
  const [liquidationBusy, setLiquidationBusy] = useState(false);
  const [liquidationError, setLiquidationError] = useState('');
  const [liquidationSnapshot, setLiquidationSnapshot] = useState<LiquidationSnapshot | null>(null);
  const [heatmapWindow, setHeatmapWindow] = useState('24h');
  const [heatmapBucket, setHeatmapBucket] = useState('15');
  const [heatmapProvider, setHeatmapProvider] = useState('all');
  const [heatmapSide, setHeatmapSide] = useState('all');
  const [heatmapData, setHeatmapData] = useState<LiquidationHeatmap | null>(null);
  const [heatmapBusy, setHeatmapBusy] = useState(false);
  const [heatmapError, setHeatmapError] = useState('');
  const [clusterWindow, setClusterWindow] = useState('24h');
  const [clusterSide, setClusterSide] = useState('all');
  const [clusterStrength, setClusterStrength] = useState('35');
  const [clusterData, setClusterData] = useState<LiquidationClusters | null>(null);
  const [clusterBusy, setClusterBusy] = useState(false);
  const [clusterError, setClusterError] = useState('');
  const [markPriceHistory, setMarkPriceHistory] = useState<MarkPriceHistory | null>(null);
  const [clusterMonitoring, setClusterMonitoring] = useState(false);
  const [proximityThreshold, setProximityThreshold] = useState('0.5');
  const [liquidationAlerts, setLiquidationAlerts] = useState<LiquidationAlert[]>([]);
  const previousClustersRef = useRef<LiquidationClusters | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/health`)
      .then((response) => setHealth(response.ok ? 'online' : 'offline'))
      .catch(() => setHealth('offline'));
    fetch(`${API_BASE}/api/v1/auth/me`, { credentials: 'include' })
      .then(async (response) => {
        if (!response.ok) return;
        const body = await response.json() as { username: string };
        setUsername(body.username);
        setCsrf(cookieValue('ca_csrf'));
        setAuthenticated(true);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!authenticated || !liveRiskMonitoring) return undefined;
    let active = true;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const response = await fetch(API_BASE + '/api/v1/derivatives/risk-assessment/live', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            symbol: symbol.trim(), side: riskSide, entry_price: riskEntry,
            quantity: riskQuantity, leverage: riskLeverage,
            extra_margin: riskExtraMargin, estimated_close_fee_rate: riskFee,
          }),
        });
        const body = await response.json().catch(() => ({})) as LiveRiskAssessment & { detail?: string };
        if (!response.ok) throw new Error(body.detail || 'Risk monitor request failed (' + response.status + ')');
        if (active) {
          setLiveRiskAssessment(body);
          setLiveRiskError('');
        }
      } catch (cause) {
        if (active) setLiveRiskError(cause instanceof Error ? cause.message : 'Risk monitor refresh failed');
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [authenticated, liveRiskMonitoring, symbol, riskSide, riskEntry, riskQuantity, riskLeverage, riskExtraMargin, riskFee]);

  const loadCandles = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      const query = new URLSearchParams({
        symbol: symbol.trim(),
        interval,
        limit,
        closed_only: String(closedOnly),
      });
      const response = await fetch(`${API_BASE}/api/v1/market-data/candles?${query}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || `Request failed (${response.status})`);
      }
      setData(await response.json() as CandleResponse);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load market data');
    } finally {
      setBusy(false);
    }
  }, [symbol, interval, limit, closedOnly]);

  useEffect(() => {
    if (authenticated) void loadCandles();
  }, [authenticated]);

  async function runBacktest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBacktestBusy(true);
    setBacktestError('');
    setBacktestResult(null);
    try {
      const start = new Date(backtestStart);
      const end = new Date(backtestEnd);
      if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end) {
        throw new Error('Choose a valid date range with start before end.');
      }
      const query = new URLSearchParams({
        symbol: symbol.trim(),
        interval,
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        fast_period: fastPeriod,
        slow_period: slowPeriod,
      });
      const response = await fetch(`${API_BASE}/api/v1/backtest/sma-crossover?${query}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || `Backtest failed (${response.status})`);
      }
      setBacktestResult(await response.json() as BacktestResponse);
    } catch (cause) {
      setBacktestError(cause instanceof Error ? cause.message : 'Could not run backtest');
    } finally {
      setBacktestBusy(false);
    }
  }

  async function loadFuturesOverview() {
    setFuturesBusy(true);
    setFuturesError('');
    setFuturesData(null);
    try {
      const query = new URLSearchParams({ symbol: symbol.trim() });
      const response = await fetch(`${API_BASE}/api/v1/derivatives/overview?${query}`, {
        credentials: 'include',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || `Futures request failed (${response.status})`);
      }
      setFuturesData(await response.json() as FuturesOverview);
    } catch (cause) {
      setFuturesError(cause instanceof Error ? cause.message : 'Could not load futures data');
    } finally {
      setFuturesBusy(false);
    }
  }

  async function loadFuturesAnalysis() {
    setAnalysisBusy(true);
    setAnalysisError('');
    setFuturesAnalysis(null);
    try {
      const query = new URLSearchParams({ symbol: symbol.trim(), period: historyPeriod });
      const response = await fetch(API_BASE + '/api/v1/derivatives/analysis?' + query, { credentials: 'include' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || 'Futures analysis failed (' + response.status + ')');
      }
      setFuturesAnalysis(await response.json() as FuturesAnalysis);
    } catch (cause) {
      setAnalysisError(cause instanceof Error ? cause.message : 'Could not analyze futures context');
    } finally {
      setAnalysisBusy(false);
    }
  }


  async function loadMarketStructure() {
    setMarketStructureBusy(true);
    setMarketStructureError('');
    setMarketStructure(null);
    try {
      const query = new URLSearchParams({
        symbol: symbol.trim(), period: marketStructurePeriod, limit: '200', pivot_window: pivotWindow,
      });
      const response = await fetch(API_BASE + '/api/v1/derivatives/market-structure?' + query, {
        credentials: 'include',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || 'Market structure request failed (' + response.status + ')');
      }
      setMarketStructure(await response.json() as MarketStructure);
    } catch (cause) {
      setMarketStructureError(cause instanceof Error ? cause.message : 'Could not analyze market structure');
    } finally {
      setMarketStructureBusy(false);
    }
  }

  async function loadConfluence() {
    setConfluenceBusy(true);
    setConfluenceError('');
    setConfluence(null);
    try {
      const query = new URLSearchParams({ symbol: symbol.trim(), limit: '200', pivot_window: pivotWindow });
      const response = await fetch(API_BASE + '/api/v1/derivatives/confluence?' + query, { credentials: 'include' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || 'Multi-timeframe confluence failed (' + response.status + ')');
      }
      setConfluence(await response.json() as MultiTimeframeConfluence);
    } catch (cause) {
      setConfluenceError(cause instanceof Error ? cause.message : 'Could not analyze multi-timeframe confluence');
    } finally {
      setConfluenceBusy(false);
    }
  }

  async function loadFuturesHistory() {
    setHistoryBusy(true);
    setHistoryError('');
    setFuturesHistory(null);
    try {
      const query = new URLSearchParams({ symbol: symbol.trim(), period: historyPeriod, limit: historyLimit });
      const response = await fetch(API_BASE + '/api/v1/derivatives/history?' + query, { credentials: 'include' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || 'Futures history request failed (' + response.status + ')');
      }
      setFuturesHistory(await response.json() as FuturesHistory);
    } catch (cause) {
      setHistoryError(cause instanceof Error ? cause.message : 'Could not load futures history');
    } finally {
      setHistoryBusy(false);
    }
  }


  async function loadRiskTiers() {
    setRiskBusy(true);
    setRiskError('');
    setRiskResult(null);
    try {
      const query = new URLSearchParams({ symbol: symbol.trim() });
      const response = await fetch(API_BASE + '/api/v1/derivatives/risk-tiers?' + query, { credentials: 'include' });
      const body = await response.json().catch(() => ({})) as { detail?: string; tiers?: RiskTier[] };
      if (!response.ok) throw new Error(body.detail || 'Risk-tier request failed (' + response.status + ')');
      if (!body.tiers?.length) throw new Error('The exchange returned no risk tiers.');
      setRiskTiersJson(JSON.stringify(body.tiers, null, 2));
    } catch (cause) {
      setRiskError(cause instanceof Error ? cause.message : 'Could not load risk tiers');
    } finally {
      setRiskBusy(false);
    }
  }

  async function runRiskEstimate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRiskBusy(true);
    setRiskError('');
    setRiskResult(null);
    try {
      const tiers = JSON.parse(riskTiersJson) as RiskTier[];
      if (!Array.isArray(tiers) || !tiers.length) throw new Error('Provide a non-empty verified risk-tier JSON array.');
      const response = await fetch(API_BASE + '/api/v1/derivatives/risk-estimate', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: symbol.trim(), side: riskSide, entry_price: riskEntry, quantity: riskQuantity,
          leverage: riskLeverage, extra_margin: riskExtraMargin,
          estimated_close_fee_rate: riskFee, risk_tiers: tiers,
        }),
      });
      const body = await response.json().catch(() => ({})) as RiskEstimate & { detail?: string };
      if (!response.ok) throw new Error(body.detail || 'Risk estimate failed (' + response.status + ')');
      setRiskResult(body);
    } catch (cause) {
      setRiskError(cause instanceof Error ? cause.message : 'Could not calculate liquidation estimate');
    } finally {
      setRiskBusy(false);
    }
  }

  async function runLiveRiskAssessment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLiveRiskBusy(true);
    setLiveRiskError('');
    setLiveRiskAssessment(null);
    try {
      const response = await fetch(API_BASE + '/api/v1/derivatives/risk-assessment/live', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: symbol.trim(), side: riskSide, entry_price: riskEntry,
          quantity: riskQuantity, leverage: riskLeverage,
          extra_margin: riskExtraMargin, estimated_close_fee_rate: riskFee,
        }),
      });
      const body = await response.json().catch(() => ({})) as LiveRiskAssessment & { detail?: string };
      if (!response.ok) throw new Error(body.detail || 'Live risk assessment failed (' + response.status + ')');
      setLiveRiskAssessment(body);
    } catch (cause) {
      setLiveRiskError(cause instanceof Error ? cause.message : 'Could not assess live futures risk');
    } finally {
      setLiveRiskBusy(false);
    }
  }

  async function startLiquidationStream() {
    setLiquidationBusy(true);
    setLiquidationError('');
    try {
      const query = new URLSearchParams({ symbol: symbol.trim() });
      const response = await fetch(API_BASE + '/api/v1/derivatives/liquidations/start?' + query, {
        method: 'POST', credentials: 'include',
      });
      const body = await response.json().catch(() => ({})) as LiquidationSnapshot & { detail?: string };
      if (!response.ok) throw new Error(body.detail || 'Could not start liquidation stream (' + response.status + ')');
      setLiquidationSnapshot(body);
      setLiquidationLive(true);
    } catch (cause) {
      setLiquidationError(cause instanceof Error ? cause.message : 'Could not start liquidation stream');
    } finally {
      setLiquidationBusy(false);
    }
  }

  async function stopLiquidationStream() {
    setLiquidationBusy(true);
    setLiquidationError('');
    try {
      const query = new URLSearchParams({ symbol: symbol.trim() });
      const response = await fetch(API_BASE + '/api/v1/derivatives/liquidations/stop?' + query, {
        method: 'DELETE', credentials: 'include',
      });
      const body = await response.json().catch(() => ({})) as LiquidationSnapshot & { detail?: string };
      if (!response.ok) throw new Error(body.detail || 'Could not stop liquidation stream (' + response.status + ')');
      setLiquidationSnapshot(body);
      setLiquidationLive(false);
    } catch (cause) {
      setLiquidationError(cause instanceof Error ? cause.message : 'Could not stop liquidation stream');
    } finally {
      setLiquidationBusy(false);
    }
  }

  async function loadLiquidationClusters() {
    setClusterBusy(true);
    setClusterError('');
    setClusterData(null);
    try {
      const query = new URLSearchParams({
        symbol: symbol.trim(),
        window: clusterWindow,
        side: clusterSide,
        min_strength_pct: clusterStrength,
        include_market_context: 'true',
      });
      const response = await fetch(API_BASE + '/api/v1/derivatives/liquidations/clusters?' + query, {
        credentials: 'include',
      });
      const body = await response.json().catch(() => ({})) as LiquidationClusters & { detail?: string };
      if (!response.ok) throw new Error(body.detail || 'Liquidation cluster analysis failed (' + response.status + ')');
      setClusterData(body);
      const [overviewResult, markResult] = await Promise.allSettled([
        fetch(API_BASE + '/api/v1/derivatives/overview?' + new URLSearchParams({ symbol: symbol.trim() }), { credentials: 'include' }),
        fetch(API_BASE + '/api/v1/derivatives/mark-price-history?' + new URLSearchParams({ symbol: symbol.trim(), period: interval === '1m' || interval === '3m' ? '5m' : ['5m','15m','30m','1h','4h','1d'].includes(interval) ? interval : '1h', limit: '100' }), { credentials: 'include' }),
      ]);
      if (overviewResult.status === 'fulfilled' && overviewResult.value.ok) {
        setFuturesData(await overviewResult.value.json() as FuturesOverview);
      }
      if (markResult.status === 'fulfilled' && markResult.value.ok) {
        setMarkPriceHistory(await markResult.value.json() as MarkPriceHistory);
      }
    } catch (cause) {
      setClusterError(cause instanceof Error ? cause.message : 'Could not load liquidation clusters');
    } finally {
      setClusterBusy(false);
    }
  }

  async function loadLiquidationHeatmap() {
    setHeatmapBusy(true);
    setHeatmapError('');
    setHeatmapData(null);
    try {
      const query = new URLSearchParams({
        symbol: symbol.trim(),
        window: heatmapWindow,
        bucket_minutes: heatmapBucket,
        provider: heatmapProvider,
        side: heatmapSide,
      });
      const response = await fetch(API_BASE + '/api/v1/derivatives/liquidations/heatmap?' + query, {
        credentials: 'include',
      });
      const body = await response.json().catch(() => ({})) as LiquidationHeatmap & { detail?: string };
      if (!response.ok) throw new Error(body.detail || 'Liquidation heatmap failed (' + response.status + ')');
      setHeatmapData(body);
    } catch (cause) {
      setHeatmapError(cause instanceof Error ? cause.message : 'Could not load liquidation heatmap');
    } finally {
      setHeatmapBusy(false);
    }
  }

  useEffect(() => {
    if (!authenticated || !liquidationLive) return undefined;
    let active = true;
    const refresh = async () => {
      try {
        const query = new URLSearchParams({ symbol: symbol.trim(), limit: '100' });
        const response = await fetch(API_BASE + '/api/v1/derivatives/liquidations?' + query, { credentials: 'include' });
        const body = await response.json() as LiquidationSnapshot & { detail?: string };
        if (!response.ok) throw new Error(body.detail || 'Liquidation snapshot failed');
        if (active) setLiquidationSnapshot(body);
      } catch (cause) {
        if (active) setLiquidationError(cause instanceof Error ? cause.message : 'Liquidation stream refresh failed');
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [authenticated, liquidationLive, symbol]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password }),
      });
      const body = await response.json() as { detail?: string; csrf_token?: string };
      if (!response.ok) throw new Error(body.detail || 'Login failed');
      setCsrf(body.csrf_token || '');
      setPassword('');
      setAuthenticated(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Login failed');
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    try {
      const response = await fetch(`${API_BASE}/api/v1/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': csrf || cookieValue('ca_csrf') },
      });
      if (!response.ok) throw new Error('Logout failed');
      setAuthenticated(false);
      setCsrf('');
      setData(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Logout failed');
    } finally {
      setBusy(false);
    }
  }

  const heatmapColumns = useMemo(() => {
    if (!heatmapData) return [];
    return [...new Set(heatmapData.cells.map((cell) => cell.time_bucket))].sort().slice(-96);
  }, [heatmapData]);
  const heatmapRows = useMemo(() => {
    if (!heatmapData) return [];
    const lookup = new Map(heatmapData.cells.map((cell) => [cell.time_bucket + ':' + cell.price_bin, cell]));
    const bins = [...new Set(heatmapData.cells.map((cell) => cell.price_bin))].sort((a, b) => b - a);
    return bins.map((bin) => ({
      bin,
      cells: heatmapColumns.map((timestamp) => lookup.get(timestamp + ':' + bin) || null),
    }));
  }, [heatmapData, heatmapColumns]);
  const heatmapMaxNotional = useMemo(() => {
    if (!heatmapData?.cells.length) return 1;
    return Math.max(...heatmapData.cells.map((cell) => Number(cell.total_notional)), 1);
  }, [heatmapData]);


  useEffect(() => {
    if (!authenticated || !clusterMonitoring) return undefined;
    let active = true;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const clusterQuery = new URLSearchParams({
          symbol: symbol.trim(), window: clusterWindow, side: clusterSide,
          min_strength_pct: clusterStrength, include_market_context: 'true',
        });
        const markPeriod = ['5m','15m','30m','1h','4h','1d'].includes(interval) ? interval : '1h';
        const [clusterResponse, overviewResponse, markResponse] = await Promise.all([
          fetch(API_BASE + '/api/v1/derivatives/liquidations/clusters?' + clusterQuery, { credentials: 'include' }),
          fetch(API_BASE + '/api/v1/derivatives/overview?' + new URLSearchParams({ symbol: symbol.trim() }), { credentials: 'include' }),
          fetch(API_BASE + '/api/v1/derivatives/mark-price-history?' + new URLSearchParams({ symbol: symbol.trim(), period: markPeriod, limit: '100' }), { credentials: 'include' }),
        ]);
        if (!active) return;
        if (clusterResponse.ok) {
          const next = await clusterResponse.json() as LiquidationClusters;
          const previous = previousClustersRef.current;
          if (previous && previous.symbol === next.symbol) {
            const messages: LiquidationAlert[] = [];
            for (const cluster of next.clusters) {
              const midpoint = Number(cluster.mid_price);
              const match = previous.clusters.find((old) =>
                midpoint >= Number(old.price_from) && midpoint <= Number(old.price_to)
                || Number(old.mid_price) >= Number(cluster.price_from) && Number(old.mid_price) <= Number(cluster.price_to)
              );
              if (!match) continue;
              const before = Number(match.total_notional);
              const after = Number(cluster.total_notional);
              if (before > 0 && after >= before * 1.5) {
                messages.push({
                  id: 'concentration-' + cluster.price_from + '-' + Date.now(),
                  createdAt: new Date().toISOString(),
                  kind: 'concentration',
                  message: 'Observed liquidation notional rose by at least 50% in the ' + cluster.price_from + '–' + cluster.price_to + ' price zone between monitor snapshots.',
                });
              }
            }
            if (messages.length) setLiquidationAlerts((current) => [...messages, ...current].slice(0, 10));
          }
          previousClustersRef.current = next;
          setClusterData(next);
        }
        if (overviewResponse.ok) setFuturesData(await overviewResponse.json() as FuturesOverview);
        if (markResponse.ok) setMarkPriceHistory(await markResponse.json() as MarkPriceHistory);
      } catch {
        // A missed monitor cycle does not invalidate the last successful snapshot.
      } finally {
        refreshing = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [authenticated, clusterMonitoring, symbol, clusterWindow, clusterSide, clusterStrength, interval]);

  const markPriceChart = useMemo(() => {
    const candles = markPriceHistory?.candles || [];
    if (!candles.length) return { points: '', bands: [] as { key: string; y: number; height: number; label: string }[], min: 0, max: 0 };
    const prices = candles.flatMap((candle) => [Number(candle.low), Number(candle.high)]);
    const clusters = clusterData?.clusters || [];
    const min = Math.min(...prices, ...clusters.map((cluster) => Number(cluster.price_from)));
    const max = Math.max(...prices, ...clusters.map((cluster) => Number(cluster.price_to)));
    const span = max - min || 1;
    const yFor = (price: number) => 172 - ((price - min) / span) * 150;
    const points = candles.map((candle, index) => {
      const x = 8 + (index / Math.max(candles.length - 1, 1)) * 784;
      return x.toFixed(1) + ',' + yFor(Number(candle.close)).toFixed(1);
    }).join(' ');
    const bands = clusters.map((cluster) => {
      const top = yFor(Number(cluster.price_to));
      const bottom = yFor(Number(cluster.price_from));
      return {
        key: cluster.price_from + '-' + cluster.price_to,
        y: Math.min(top, bottom),
        height: Math.max(2, Math.abs(bottom - top)),
        label: cluster.dominant_side,
      };
    });
    return { points, bands, min, max };
  }, [markPriceHistory, clusterData]);

  const proximityAlert = useMemo(() => {
    if (!futuresData || !clusterData?.clusters.length) return null;
    const mark = Number(futuresData.mark_price);
    if (!Number.isFinite(mark) || mark <= 0) return null;
    const threshold = Number(proximityThreshold);
    const candidates = clusterData.clusters.map((cluster) => {
      const low = Number(cluster.price_from);
      const high = Number(cluster.price_to);
      const gap = mark < low ? low - mark : mark > high ? mark - high : 0;
      return { cluster, distancePct: gap / mark * 100 };
    }).sort((a, b) => a.distancePct - b.distancePct);
    const nearest = candidates[0];
    return { ...nearest, triggered: nearest.distancePct <= threshold };
  }, [futuresData, clusterData, proximityThreshold]);

  const points = useMemo(() => {
    if (!data?.candles.length) return '';
    const values = data.candles.map((candle) => Number(candle.close));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return values.map((value, index) => {
      const x = 8 + (index / Math.max(values.length - 1, 1)) * 784;
      const y = 172 - ((value - min) / span) * 150;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  }, [data]);

  if (!authenticated) {
    return (
      <main className="shell auth-shell">
        <header className="topbar"><div className="brand-mark">CA</div><div><strong>ChartAnalyzer</strong><small>Quantitative research workspace</small></div><span className={`status ${health}`}>{health}</span></header>
        <section className="login-card">
          <p className="eyebrow">PRIVATE WORKSPACE</p>
          <h1>Sign in to continue</h1>
          <p className="muted">Use the local administrator account configured in your backend environment.</p>
          <form onSubmit={login} className="stack">
            <label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required /></label>
            <label>Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required /></label>
            <button disabled={busy}>{busy ? 'Please wait…' : 'Sign in'}</button>
          </form>
          {error && <p className="error" role="alert">{error}</p>}
          <p className="footnote">API status: {health}. This local MVP does not provide trading execution.</p>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand-mark">CA</div>
        <div><strong>ChartAnalyzer</strong><small>Market research</small></div>
        <span className={`status ${health}`}>{health}</span>
        <button className="quiet-button" onClick={logout} disabled={busy}>Sign out</button>
      </header>
      <section className="intro">
        <div><p className="eyebrow">MARKET DATA WORKSPACE</p><h1>Explore the market.</h1><p className="muted">Authenticated Spot OHLCV data from your configured exchange provider.</p></div>
        <span className="pill">Read-only · No trading</span>
      </section>
      <section className="panel">
        <form className="controls" onSubmit={(event) => { event.preventDefault(); void loadCandles(); }}>
          <label>Market symbol<input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} maxLength={30} required /></label>
          <label>Timeframe<select value={interval} onChange={(event) => setInterval(event.target.value)}>{['1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d','3d','1w','1M'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Candles<select value={limit} onChange={(event) => setLimit(event.target.value)}>{['50','100','200','300','500','1000'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label className="toggle"><input type="checkbox" checked={closedOnly} onChange={(event) => setClosedOnly(event.target.checked)} /> Closed candles only</label>
          <button disabled={busy}>{busy ? 'Loading…' : 'Load chart'}</button>
        </form>
      </section>
      {error && <p className="error" role="alert">{error}</p>}
      <section className="panel chart-panel">
        <div className="section-heading"><div><p className="eyebrow">PRICE SERIES</p><h2>{data?.symbol || symbol} <span>{data?.interval || interval}</span></h2></div>{data && <div className="meta"><strong>{data.count}</strong><small>candles · {data.provider}</small></div>}</div>
        {data?.candles.length ? <>
          <div className="chart-wrap"><svg viewBox="0 0 800 190" role="img" aria-label={`Closing price chart for ${data.symbol}`}><line x1="8" y1="20" x2="792" y2="20" /><line x1="8" y1="95" x2="792" y2="95" /><line x1="8" y1="172" x2="792" y2="172" /><polyline points={points} /></svg></div>
          <div className="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th><th>Status</th></tr></thead><tbody>{[...data.candles].reverse().slice(0, 12).map((candle) => <tr key={candle.timestamp}><td>{new Date(candle.timestamp).toLocaleString('en-US', { timeZone: 'UTC' })}</td><td>{candle.open}</td><td>{candle.high}</td><td>{candle.low}</td><td>{candle.close}</td><td>{candle.volume}</td><td><span className={candle.is_closed ? 'closed' : 'open'}>{candle.is_closed ? 'Closed' : 'Open'}</span></td></tr>)}</tbody></table></div>
        </> : <div className="empty-state">{busy ? 'Loading market data…' : 'No candles loaded. Select a symbol and timeframe to begin.'}</div>}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">DERIVATIVES MARKET CONTEXT</p><h2>{symbol} linear perpetual</h2></div>
          <span className="pill">Read-only · Futures</span>
        </div>
        <div className="futures-actions">
          <p className="footnote">Spot candles and futures statistics are separate market feeds. This view does not place orders.</p>
          <button onClick={() => void loadFuturesOverview()} disabled={futuresBusy || busy}>{futuresBusy ? 'Loading…' : 'Load futures overview'}</button>
        </div>
        {futuresError && <p className="error" role="alert">{futuresError}</p>}
        {futuresData && <>
          <div className="stats-grid">
            <div><small>Mark price</small><strong>{Number(futuresData.mark_price).toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong></div>
            <div><small>Index price</small><strong>{Number(futuresData.index_price).toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong></div>
            <div><small>Funding rate</small><strong>{(Number(futuresData.funding_rate) * 100).toFixed(4)}%</strong></div>
            <div><small>Open interest</small><strong>{Number(futuresData.open_interest).toLocaleString(undefined, { maximumFractionDigits: 4 })}</strong></div>
            <div><small>OI notional (quote)</small><strong>{Number(futuresData.open_interest_value).toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong></div>
            <div><small>Next funding</small><strong>{futuresData.next_funding_time ? new Date(futuresData.next_funding_time).toLocaleString() : '—'}</strong></div>
          </div>
          <p className="footnote">Provider: {futuresData.provider} · {futuresData.market_type} · Snapshot: {new Date(futuresData.observed_at).toLocaleString()}</p>
        </>}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">FUTURES HISTORY</p><h2>Funding · Open interest · Long/short</h2></div>
          <span className="pill">Historical · Read-only</span>
        </div>
        <div className="controls history-controls">
          <label>Period<select value={historyPeriod} onChange={(event) => setHistoryPeriod(event.target.value)}>{['5m','15m','30m','1h','4h','1d'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Records<select value={historyLimit} onChange={(event) => setHistoryLimit(event.target.value)}>{['30','50','100','200'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button onClick={() => void loadFuturesHistory()} disabled={historyBusy || busy}>{historyBusy ? 'Loading…' : 'Load futures history'}</button>
        </div>
        <p className="footnote">Long/short is a ratio of global accounts where supported. Exchange history windows and record availability differ.</p>
        {historyError && <p className="error" role="alert">{historyError}</p>}
        {futuresHistory && <div className="series-grid">
          <div className="series-card"><small>Funding rate</small><strong>{futuresHistory.funding_rate.length ? (Number(futuresHistory.funding_rate[futuresHistory.funding_rate.length - 1].value) * 100).toFixed(4) + '%' : 'No data'}</strong><svg viewBox="0 0 800 190" role="img" aria-label="Funding rate history"><polyline points={seriesPolyline(futuresHistory.funding_rate)} /></svg><span>{futuresHistory.funding_rate.length} points</span></div>
          <div className="series-card"><small>Open interest</small><strong>{futuresHistory.open_interest.length ? Number(futuresHistory.open_interest[futuresHistory.open_interest.length - 1].value).toLocaleString(undefined, { maximumFractionDigits: 3 }) : 'No data'}</strong><svg viewBox="0 0 800 190" role="img" aria-label="Open interest history"><polyline points={seriesPolyline(futuresHistory.open_interest)} /></svg><span>{futuresHistory.open_interest.length} points</span></div>
          <div className="series-card"><small>Long / short account ratio</small><strong>{futuresHistory.long_short_ratio.length ? Number(futuresHistory.long_short_ratio[futuresHistory.long_short_ratio.length - 1].value).toFixed(3) : 'No data'}</strong><svg viewBox="0 0 800 190" role="img" aria-label="Long short ratio history"><polyline points={seriesPolyline(futuresHistory.long_short_ratio)} /></svg><span>{futuresHistory.long_short_ratio.length} points</span></div>
        </div>}
        {futuresHistory && <p className="footnote">Source: {futuresHistory.provider} · {futuresHistory.symbol} · {futuresHistory.period} · Updated {new Date(futuresHistory.observed_at).toLocaleString()}</p>}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">FUTURES INTELLIGENCE</p><h2>Positioning context analysis</h2></div>
          <span className="pill">Rule-based · Not a trade signal</span>
        </div>
        <div className="controls history-controls">
          <label>Analysis interval<select value={historyPeriod} onChange={(event) => setHistoryPeriod(event.target.value)}>{['5m','15m','30m','1h','4h','1d'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button onClick={() => void loadFuturesAnalysis()} disabled={analysisBusy || busy}>{analysisBusy ? 'Analyzing…' : 'Analyze futures context'}</button>
        </div>
        <p className="footnote">Combines futures Mark Price movement with Open Interest, Funding Rate and account ratio. It is descriptive, not predictive.</p>
        {analysisError && <p className="error" role="alert">{analysisError}</p>}
        {futuresAnalysis && <>
          <div className="stats-grid">
            <div><small>Mark Price change</small><strong>{futuresAnalysis.price_change_pct === null ? '—' : Number(futuresAnalysis.price_change_pct).toFixed(3) + '%'}</strong></div>
            <div><small>Open interest change</small><strong>{futuresAnalysis.open_interest_change_pct === null ? '—' : Number(futuresAnalysis.open_interest_change_pct).toFixed(3) + '%'}</strong></div>
            <div><small>Latest funding</small><strong>{futuresAnalysis.latest_funding_rate === null ? '—' : (Number(futuresAnalysis.latest_funding_rate) * 100).toFixed(4) + '%'}</strong></div>
            <div><small>Long / short ratio</small><strong>{futuresAnalysis.latest_long_short_ratio === null ? '—' : Number(futuresAnalysis.latest_long_short_ratio).toFixed(3)}</strong></div>
            <div><small>Positioning interpretation</small><strong>{futuresAnalysis.positioning.replace(/_/g, ' ')}</strong></div>
            <div><small>Squeeze conditions</small><strong>{futuresAnalysis.squeeze_risk.replace(/_/g, ' ')}</strong></div>
            <div><small>Funding bias</small><strong>{futuresAnalysis.funding_bias.replace(/_/g, ' ')}</strong></div>
            <div><small>Confidence</small><strong>{futuresAnalysis.confidence}</strong></div>
          </div>
          <div className="signal-list">{futuresAnalysis.signals.map((signal) => <article className="signal-item" key={signal.code}>
            <div><strong>{signal.label}</strong><span>{signal.direction.replace(/_/g, ' ')} · {signal.severity}</span></div>
            <p>{signal.explanation}</p>
          </article>)}</div>
          <div className="caveat-list"><strong>Interpretation limits</strong><ul>{futuresAnalysis.caveats.map((item) => <li key={item}>{item}</li>)}</ul></div>
          <p className="footnote">Updated {new Date(futuresAnalysis.observed_at).toLocaleString()} · {futuresAnalysis.symbol} · {futuresAnalysis.period}</p>
        </>}
      </section>


      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">MULTI-TIMEFRAME CONFLUENCE</p><h2>{symbol} alignment map</h2></div>
          <span className="pill">5m · 15m · 1h · 4h · Closed Mark Price</span>
        </div>
        <div className="controls history-controls">
          <label>Pivot confirmation window<select value={pivotWindow} onChange={(event) => setPivotWindow(event.target.value)}>{['2','3','4','5','6','8','10'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button onClick={() => void loadConfluence()} disabled={confluenceBusy || busy}>{confluenceBusy ? 'Comparing timeframes…' : 'Analyze confluence'}</button>
        </div>
        <p className="footnote">Compares confirmed market structure from the same configured futures venue. Neutral timeframes remain neutral; conflicting directions are not collapsed into a forced buy/sell signal.</p>
        {confluenceError && <p className="error" role="alert">{confluenceError}</p>}
        {confluence && <>
          <div className="stats-grid">
            <div><small>Alignment</small><strong>{confluence.alignment.replace(/_/g, ' ')}</strong></div>
            <div><small>Dominant bias</small><strong>{confluence.dominant_bias}</strong></div>
            <div><small>Bullish timeframes</small><strong>{confluence.bullish_timeframes}</strong></div>
            <div><small>Bearish timeframes</small><strong>{confluence.bearish_timeframes}</strong></div>
            <div><small>Neutral timeframes</small><strong>{confluence.neutral_timeframes}</strong></div>
          </div>
          {confluence.unavailable_timeframes.length > 0 && <p className="error">Unavailable data: {confluence.unavailable_timeframes.join(', ')}. Alignment is incomplete.</p>}
          <div className="table-wrap">
            <table><thead><tr><th>Timeframe</th><th>Bias</th><th>Structural trend</th><th>Last event</th><th>Active bullish FVG / OB</th><th>Active bearish FVG / OB</th><th>Closed candles</th></tr></thead>
              <tbody>{confluence.timeframes.map((item) => <tr key={item.period}>
                <td>{item.period}</td><td>{item.bias}</td><td>{item.trend.replace(/_/g, ' ')}</td><td>{item.latest_event || '—'}</td>
                <td>{item.active_bullish_fvgs} / {item.active_bullish_order_blocks}</td>
                <td>{item.active_bearish_fvgs} / {item.active_bearish_order_blocks}</td><td>{item.closed_candles}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <div className="caveat-list"><strong>Evidence by timeframe</strong><ul>{confluence.timeframes.flatMap((item) => item.evidence.map((evidence, index) => <li key={item.period + index}><strong>{item.period}:</strong> {evidence}</li>))}</ul></div>
          <div className="caveat-list"><strong>Method and limitations</strong><ul>{confluence.caveats.map((item) => <li key={item}>{item}</li>)}</ul></div>
          <p className="footnote">Provider: {confluence.provider} · {confluence.symbol} · Updated {new Date(confluence.observed_at).toLocaleString()}</p>
        </>}
        {!confluence && !confluenceBusy && !confluenceError && <div className="empty-state">Run the confluence analysis to compare the four structural timeframes.</div>}
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">MARKET STRUCTURE INTELLIGENCE</p><h2>{symbol} structure map</h2></div>
          <span className="pill">Closed Mark Price candles · Rule-based</span>
        </div>
        <div className="controls history-controls">
          <label>Structure timeframe<select value={marketStructurePeriod} onChange={(event) => setMarketStructurePeriod(event.target.value)}>{['5m','15m','30m','1h','4h','1d'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Pivot confirmation window<select value={pivotWindow} onChange={(event) => setPivotWindow(event.target.value)}>{['2','3','4','5','6','8','10'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <button onClick={() => void loadMarketStructure()} disabled={marketStructureBusy || busy}>{marketStructureBusy ? 'Analyzing…' : 'Analyze structure'}</button>
        </div>
        <p className="footnote">Detects confirmed swings, BOS/CHoCH, liquidity sweeps, three-candle FVGs with lifecycle states, and opposing-candle Order Blocks tied to confirmed structure breaks. Pivot confirmation is delayed by the selected number of right-side candles.</p>
        {marketStructureError && <p className="error" role="alert">{marketStructureError}</p>}
        {marketStructure && <>
          <div className="stats-grid">
            <div><small>Structure bias</small><strong>{marketStructure.trend.replace(/_/g, ' ')}</strong></div>
            <div><small>Latest Mark Price close</small><strong>{marketStructure.latest_close ? Number(marketStructure.latest_close).toLocaleString() : '—'}</strong></div>
            <div><small>Confirmed swings</small><strong>{marketStructure.swings.length}</strong></div>
            <div><small>Structure events</small><strong>{marketStructure.events.length}</strong></div>
            <div><small>Detected FVG zones</small><strong>{marketStructure.fair_value_gaps.length}</strong></div>
            <div><small>Order Blocks</small><strong>{marketStructure.order_blocks.length}</strong></div>
            <div><small>Closed candles</small><strong>{marketStructure.closed_candles}</strong></div>
          </div>
          {marketStructure.events.length ? <div className="signal-list">{marketStructure.events.map((event, index) => <article className="signal-item" key={event.code + event.timestamp + index}>
            <div><strong>{event.code.replace(/_/g, ' ').toUpperCase()} · {event.direction}</strong><span>{Number(event.level).toLocaleString()} · {new Date(event.timestamp).toLocaleString()}</span></div>
            <p>{event.explanation}</p>
          </article>)}</div> : <div className="empty-state">No new structure break or sweep was confirmed on the latest closed candle.</div>}
          <div className="table-wrap">
            <table><thead><tr><th>FVG direction</th><th>Lower bound</th><th>Upper bound</th><th>Midpoint</th><th>State</th><th>Mitigated</th><th>Invalidated</th></tr></thead>
              <tbody>{marketStructure.fair_value_gaps.slice(-10).reverse().map((gap, index) => <tr key={gap.start_time + gap.end_time + index}>
                <td>{gap.direction.toUpperCase()}</td><td>{Number(gap.lower).toLocaleString()}</td><td>{Number(gap.upper).toLocaleString()}</td><td>{Number(gap.midpoint).toLocaleString()}</td><td>{gap.status.replace(/_/g, ' ')}</td><td>{gap.mitigation_time ? new Date(gap.mitigation_time).toLocaleString() : '—'}</td><td>{gap.invalidation_time ? new Date(gap.invalidation_time).toLocaleString() : '—'}</td>
              </tr>)}</tbody>
            </table>
          </div>

          <div className="table-wrap">
            <table><thead><tr><th>Order Block</th><th>Lower bound</th><th>Upper bound</th><th>Broken swing</th><th>State</th><th>Created</th><th>Mitigated</th><th>Invalidated</th></tr></thead>
              <tbody>{marketStructure.order_blocks.slice(-10).reverse().map((block, index) => <tr key={block.start_time + block.direction + index}>
                <td>{block.direction.toUpperCase()}</td><td>{Number(block.lower).toLocaleString()}</td><td>{Number(block.upper).toLocaleString()}</td><td>{Number(block.source_swing_level).toLocaleString()}</td><td>{block.status}</td><td>{new Date(block.break_time).toLocaleString()}</td><td>{block.mitigation_time ? new Date(block.mitigation_time).toLocaleString() : '—'}</td><td>{block.invalidation_time ? new Date(block.invalidation_time).toLocaleString() : '—'}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table><thead><tr><th>Swing type</th><th>Price</th><th>Swing candle</th></tr></thead>
              <tbody>{marketStructure.swings.slice(-12).reverse().map((swing, index) => <tr key={swing.timestamp + swing.kind + index}>
                <td>{swing.kind.toUpperCase()}</td><td>{Number(swing.price).toLocaleString()}</td><td>{new Date(swing.timestamp).toLocaleString()}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <div className="caveat-list"><strong>Method and limitations</strong><ul>{marketStructure.caveats.map((item) => <li key={item}>{item}</li>)}</ul></div>
          <p className="footnote">Provider: {marketStructure.provider} · {marketStructure.symbol} · {marketStructure.period} · Updated {new Date(marketStructure.observed_at).toLocaleString()}</p>
        </>}
        {!marketStructure && !marketStructureBusy && !marketStructureError && <div className="empty-state">Run the structure analysis to inspect confirmed swings and structural events for this symbol.</div>}
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">FUTURES RISK ENGINE V3</p><h2>{symbol} live position risk</h2></div>
          <span className="pill">Exchange Mark Price + Bybit Risk Tiers</span>
        </div>
        <p className="footnote">The integrated assessment fetches a fresh Mark Price snapshot and exchange-provided Bybit Linear risk tiers. It does not accept manual tiers and does not place orders.</p>
        <form className="controls risk-controls" onSubmit={runLiveRiskAssessment}>
          <label>Position side<select value={riskSide} onChange={(event) => setRiskSide(event.target.value as 'long' | 'short')}><option value="long">Long</option><option value="short">Short</option></select></label>
          <label>Entry price<input type="number" min="0" step="any" value={riskEntry} onChange={(event) => setRiskEntry(event.target.value)} required /></label>
          <label>Quantity (base asset)<input type="number" min="0" step="any" value={riskQuantity} onChange={(event) => setRiskQuantity(event.target.value)} required /></label>
          <label>Leverage<input type="number" min="1" step="any" value={riskLeverage} onChange={(event) => setRiskLeverage(event.target.value)} required /></label>
          <label>Extra isolated margin<input type="number" min="0" step="any" value={riskExtraMargin} onChange={(event) => setRiskExtraMargin(event.target.value)} /></label>
          <label>Estimated close-fee rate<input type="number" min="0" step="any" value={riskFee} onChange={(event) => setRiskFee(event.target.value)} /></label>
          <button disabled={liveRiskBusy || busy}>{liveRiskBusy ? 'Assessing…' : 'Assess live risk'}</button>
          <button type="button" className={liveRiskMonitoring ? 'monitor-active' : 'quiet-button'} onClick={() => setLiveRiskMonitoring((value) => !value)} disabled={busy}>{liveRiskMonitoring ? 'Stop 5s monitoring' : 'Start 5s monitoring'}</button>
        </form>
        {liveRiskError && <p className="error" role="alert">{liveRiskError}</p>}
        {liveRiskAssessment && <>
          <div className={'risk-alert risk-' + liveRiskAssessment.alert.level} role="status">
            <div><strong>{liveRiskAssessment.alert.title}</strong><span>{liveRiskAssessment.alert.message}</span></div>
            <strong>{Number(liveRiskAssessment.alert.distance_percent).toFixed(3)}%</strong>
          </div>
          <div className="stats-grid">
            <div><small>Live Mark Price</small><strong>{Number(liveRiskAssessment.mark_price).toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong></div>
            <div><small>Estimated Liquidation</small><strong>{Number(liveRiskAssessment.estimate.liquidation_price_estimate).toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong></div>
            <div><small>Distance from Mark</small><strong>{liveRiskAssessment.estimate.distance_to_liquidation_from_mark_percent === null ? '—' : Number(liveRiskAssessment.estimate.distance_to_liquidation_from_mark_percent).toFixed(3) + '%'}</strong></div>
            <div><small>Bankruptcy estimate</small><strong>{Number(liveRiskAssessment.estimate.bankruptcy_price_estimate).toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong></div>
            <div><small>Exchange risk tier</small><strong>{liveRiskAssessment.estimate.liquidation_tier}</strong></div>
            <div><small>Mark snapshot age</small><strong>{liveRiskAssessment.freshness_seconds}s</strong></div>
          </div>
          <p className="footnote">Provider: {liveRiskAssessment.provider} · Tier source: {liveRiskAssessment.risk_tiers_source} · Mark observed: {new Date(liveRiskAssessment.mark_price_observed_at).toLocaleString()} · Tiers fetched: {new Date(liveRiskAssessment.risk_tiers_observed_at).toLocaleString()}</p>
          <div className="caveat-list"><strong>Risk model limits</strong><ul>{liveRiskAssessment.estimate.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></div>
        </>}
        {!liveRiskAssessment && !liveRiskBusy && !liveRiskError && <div className="empty-state">Enter the position details and assess against the configured exchange's live market snapshot and risk tiers.</div>}
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">FUTURES RISK LAB</p><h2>Isolated liquidation estimator</h2></div>
          <span className="pill">Estimate only · No orders</span>
        </div>
        <p className="footnote">Uses the supplied exchange maintenance-margin tiers. Bybit tiers can be loaded automatically; Binance account brackets must be supplied from a verified signed-account response.</p>
        <form className="controls risk-controls" onSubmit={runRiskEstimate}>
          <label>Position side<select value={riskSide} onChange={(event) => setRiskSide(event.target.value as 'long' | 'short')}><option value="long">Long</option><option value="short">Short</option></select></label>
          <label>Entry price<input type="number" min="0" step="any" value={riskEntry} onChange={(event) => setRiskEntry(event.target.value)} required /></label>
          <label>Quantity (base asset)<input type="number" min="0" step="any" value={riskQuantity} onChange={(event) => setRiskQuantity(event.target.value)} required /></label>
          <label>Leverage<input type="number" min="0" step="any" value={riskLeverage} onChange={(event) => setRiskLeverage(event.target.value)} required /></label>
          <label>Extra isolated margin<input type="number" min="0" step="any" value={riskExtraMargin} onChange={(event) => setRiskExtraMargin(event.target.value)} /></label>
          <label>Estimated close-fee rate<input type="number" min="0" step="any" value={riskFee} onChange={(event) => setRiskFee(event.target.value)} /></label>
          <button type="button" onClick={() => void loadRiskTiers()} disabled={riskBusy}>{riskBusy ? 'Loading…' : 'Load exchange tiers'}</button>
          <button disabled={riskBusy}>{riskBusy ? 'Calculating…' : 'Estimate liquidation'}</button>
        </form>
        <label className="json-field">Verified risk tiers (JSON)
          <textarea value={riskTiersJson} onChange={(event) => setRiskTiersJson(event.target.value)} rows={7} spellCheck={false} placeholder={'Paste the exchange risk tier array here, or load it automatically when supported.'} />
        </label>
        {riskError && <p className="error" role="alert">{riskError}</p>}
        {riskResult && <>
          <div className="stats-grid">
            <div><small>Estimated liquidation price</small><strong>{Number(riskResult.liquidation_price_estimate).toLocaleString(undefined, { maximumFractionDigits: 8 })}</strong></div>
            <div><small>Distance to liquidation</small><strong>{Number(riskResult.distance_to_liquidation_percent).toFixed(3)}%</strong></div>
            <div><small>Initial margin estimate</small><strong>{Number(riskResult.initial_margin_estimate).toLocaleString(undefined, { maximumFractionDigits: 4 })}</strong></div>
            <div><small>Maintenance margin estimate</small><strong>{Number(riskResult.maintenance_margin_estimate).toLocaleString(undefined, { maximumFractionDigits: 4 })}</strong></div>
            <div><small>Estimated close fee</small><strong>{Number(riskResult.estimated_close_fee).toLocaleString(undefined, { maximumFractionDigits: 4 })}</strong></div>
            <div><small>Risk tier</small><strong>{riskResult.liquidation_tier}</strong></div>
          </div>
          <div className="caveat-list"><strong>Calculation assumptions</strong><ul>{riskResult.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></div>
        </>}
      </section>
      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">LIVE LIQUIDATION TAPE</p><h2>{symbol} liquidation events</h2></div>
          <span className="pill">{liquidationSnapshot?.status.state || 'Not started'}</span>
        </div>
        <div className="futures-actions">
          <p className="footnote">Public exchange stream. Events are stored in PostgreSQL when configured; a bounded 2,000-event in-memory buffer remains as a fallback. No exchange backfill is available.</p>
          <button onClick={() => void startLiquidationStream()} disabled={liquidationBusy || liquidationLive}>{liquidationBusy ? 'Please wait…' : 'Start live stream'}</button>
          <button className="quiet-button" onClick={() => void stopLiquidationStream()} disabled={liquidationBusy || !liquidationLive}>Stop stream</button>
        </div>
        {liquidationError && <p className="error" role="alert">{liquidationError}</p>}
        {liquidationSnapshot?.status.last_error && <p className="footnote">Connection note: {liquidationSnapshot.status.last_error}</p>}
        {liquidationSnapshot?.status.persistence_error && <p className="footnote">Storage note: {liquidationSnapshot.status.persistence_error}</p>}
        <div className="stats-grid">
          <div><small>Stream status</small><strong>{liquidationSnapshot?.status.state || 'Stopped'}</strong></div>
          <div><small>Events returned</small><strong>{liquidationSnapshot?.status.event_count ?? 0}</strong></div>
          <div><small>Durable storage</small><strong>{liquidationSnapshot?.storage_status || liquidationSnapshot?.status.persistence_state || 'Not configured'}</strong></div>
          <div><small>Last event (UTC)</small><strong>{liquidationSnapshot?.status.last_event_time ? new Date(liquidationSnapshot.status.last_event_time).toLocaleTimeString() : '—'}</strong></div>
        </div>
        {liquidationSnapshot?.events.length ? <div className="table-wrap"><table><thead><tr><th>Time (local)</th><th>Liquidated side</th><th>Qty</th><th>Price</th><th>Notional</th><th>Price basis</th></tr></thead><tbody>
          {liquidationSnapshot.events.map((item) => <tr key={item.event_id}><td>{new Date(item.event_time).toLocaleString()}</td><td><span className={item.liquidation_side === 'long' ? 'open' : 'closed'}>{item.liquidation_side.toUpperCase()}</span></td><td>{item.quantity}</td><td>{item.price}</td><td>{Number(item.notional).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td><td>{item.price_kind.replace(/_/g, ' ')}</td></tr>)}
        </tbody></table></div> : <div className="empty-state">No liquidation events received yet. Start the stream and wait for exchange updates.</div>}
      </section>


      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">OBSERVED LIQUIDATION HEATMAP</p><h2>{symbol} price / time distribution</h2></div>
          <span className="pill">Historical events · Not predictive</span>
        </div>
        <div className="controls heatmap-controls">
          <label>Lookback<select value={heatmapWindow} onChange={(event) => setHeatmapWindow(event.target.value)}>{['1h','6h','24h','3d','7d'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Time bucket<select value={heatmapBucket} onChange={(event) => setHeatmapBucket(event.target.value)}>{['1','5','15','60'].map((value) => <option key={value} value={value}>{value} min</option>)}</select></label>
          <label>Exchange<select value={heatmapProvider} onChange={(event) => setHeatmapProvider(event.target.value)}><option value="all">All exchanges</option><option value="binance_usdm_futures">Binance USDⓈ-M</option><option value="bybit_linear_futures">Bybit Linear</option></select></label>
          <label>Liquidated side<select value={heatmapSide} onChange={(event) => setHeatmapSide(event.target.value)}><option value="all">Long + Short</option><option value="long">Long liquidations</option><option value="short">Short liquidations</option></select></label>
          <button onClick={() => void loadLiquidationHeatmap()} disabled={heatmapBusy}>{heatmapBusy ? 'Loading…' : 'Load heatmap'}</button>
        </div>
        {heatmapError && <p className="error" role="alert">{heatmapError}</p>}
        {heatmapData && <>
          <div className="stats-grid">
            <div><small>Observed events</small><strong>{heatmapData.total_events.toLocaleString()}</strong></div>
            <div><small>Observed notional</small><strong>{Number(heatmapData.total_notional).toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong></div>
            <div><small>Exchanges represented</small><strong>{heatmapData.providers.length}</strong></div>
          </div>
          {heatmapData.truncated && <p className="footnote">The query reached its event cap; this view may not include every event in the selected window.</p>}
          {heatmapRows.length ? <div className="heatmap-scroll">
            <div className="heatmap-axis"><span>Price band (high → low)</span><span>Recent time buckets (UTC) · up to 96 shown</span></div>
            <div className="heatmap-grid" style={{ gridTemplateColumns: 'repeat(' + heatmapColumns.length + ', minmax(12px, 1fr))' }}>
              {heatmapRows.flatMap((row) => {
                const first = row.cells.find((cell) => cell !== null);
                const label = first ? Number(first.price_from).toLocaleString(undefined, { maximumFractionDigits: 4 }) + ' – ' + Number(first.price_to).toLocaleString(undefined, { maximumFractionDigits: 4 }) : 'Bin ' + row.bin;
                return [
                  <div className="heatmap-price-label" key={'label-' + row.bin}>{label}</div>,
                  ...row.cells.map((cell, index) => <div
                    key={row.bin + '-' + heatmapColumns[index]}
                    className={'heat-cell ' + (cell ? (Number(cell.long_notional) > Number(cell.short_notional) ? 'long' : Number(cell.short_notional) > Number(cell.long_notional) ? 'short' : 'mixed') : 'empty')}
                    style={{ opacity: cell ? Math.max(0.18, Number(cell.total_notional) / heatmapMaxNotional) : 0.12 }}
                    title={cell ? new Date(cell.time_bucket).toLocaleString() + ' · ' + label + ' · ' + cell.events + ' events · long ' + cell.long_events + ' / short ' + cell.short_events + ' · notional ' + Number(cell.total_notional).toLocaleString() : 'No observed events'}
                  />),
                ];
              })}
            </div>
            <div className="heatmap-time-labels">
              {heatmapColumns.filter((_, index) => index === 0 || index === heatmapColumns.length - 1 || index % Math.max(1, Math.floor(heatmapColumns.length / 6)) === 0).map((timestamp) => <span key={timestamp}>{new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>)}
            </div>
          </div> : <div className="empty-state">No persisted liquidation events match these filters in the selected window.</div>}
          <p className="footnote">{heatmapData.data_semantics} · {heatmapData.truncated ? 'Result capped' : 'All queried rows included'} · {heatmapData.window} window</p>
        </>}
        {!heatmapData && !heatmapBusy && !heatmapError && <div className="empty-state">Load the heatmap to inspect persisted liquidation events by price and time. PostgreSQL storage is required.</div>}
      </section>


      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">LIQUIDATION CLUSTER ANALYSIS</p><h2>{symbol} observed price concentrations</h2></div>
          <span className="pill">Descriptive · Not a forecast</span>
        </div>
        <div className="controls cluster-controls">
          <label>Lookback<select value={clusterWindow} onChange={(event) => setClusterWindow(event.target.value)}>{['1h','6h','24h','3d','7d'].map((value) => <option key={value}>{value}</option>)}</select></label>
          <label>Liquidated side<select value={clusterSide} onChange={(event) => setClusterSide(event.target.value)}><option value="all">Long + Short</option><option value="long">Long</option><option value="short">Short</option></select></label>
          <label>Minimum band strength<select value={clusterStrength} onChange={(event) => setClusterStrength(event.target.value)}>{['20','35','50','65','80'].map((value) => <option key={value} value={value}>{value}% of strongest band</option>)}</select></label>
          <button onClick={() => void loadLiquidationClusters()} disabled={clusterBusy}>{clusterBusy ? 'Analyzing…' : 'Detect clusters'}</button>
        </div>
        {clusterError && <p className="error" role="alert">{clusterError}</p>}
        {clusterData && <>
          <div className="intelligence-toolbar">
            <div><small>Futures Mark Price</small><strong>{futuresData ? Number(futuresData.mark_price).toLocaleString(undefined, { maximumFractionDigits: 4 }) : 'Not loaded'}</strong></div>
            <label>Proximity threshold<select value={proximityThreshold} onChange={(event) => setProximityThreshold(event.target.value)}>{['0.25','0.5','1','2'].map((value) => <option key={value} value={value}>{value}%</option>)}</select></label>
            <button className={clusterMonitoring ? 'monitor-active' : 'quiet-button'} onClick={() => setClusterMonitoring((value) => !value)}>{clusterMonitoring ? 'Stop 30s monitor' : 'Start 30s monitor'}</button>
          </div>
          {proximityAlert && <div className={proximityAlert.triggered ? 'proximity-alert triggered' : 'proximity-alert'}>
            <strong>{proximityAlert.triggered ? 'Price near an observed liquidation zone' : 'Nearest observed liquidation zone'}</strong>
            <span>Mark Price is {proximityAlert.distancePct.toFixed(3)}% from {proximityAlert.cluster.price_from}–{proximityAlert.cluster.price_to} ({proximityAlert.cluster.dominant_side} event cluster).</span>
            <small>This is proximity to historical observed events, not a prediction of open-position liquidation levels.</small>
          </div>}
          {markPriceHistory?.candles.length ? <div className="mark-chart-wrap">
            <div className="chart-legend"><span><i className="legend-price" /> Mark Price close</span><span><i className="legend-long" /> Long-dominant event cluster</span><span><i className="legend-short" /> Short-dominant event cluster</span></div>
            <svg viewBox="0 0 800 190" role="img" aria-label={symbol + ' futures mark price with observed liquidation event clusters'}>
              <line x1="8" y1="20" x2="792" y2="20" /><line x1="8" y1="95" x2="792" y2="95" /><line x1="8" y1="172" x2="792" y2="172" />
              {markPriceChart.bands.map((band) => <rect key={band.key} x="8" y={band.y} width="784" height={band.height} className={band.label === 'long' ? 'mark-cluster-long' : band.label === 'short' ? 'mark-cluster-short' : 'mark-cluster-balanced'} />)}
              <polyline points={markPriceChart.points} />
            </svg>
            <div className="chart-range"><span>{markPriceChart.max.toLocaleString(undefined, { maximumFractionDigits: 4 })}</span><span>{markPriceChart.min.toLocaleString(undefined, { maximumFractionDigits: 4 })}</span></div>
            <p className="footnote">Futures Mark Price candles and recorded liquidation price bands use the selected futures venue. Cluster bands are historical event concentrations, not estimated liquidation levels.</p>
          </div> : <div className="empty-state">Load cluster analysis to retrieve the matching futures Mark Price chart.</div>}
          {liquidationAlerts.length > 0 && <div className="alert-history">
            <strong>Recent monitor alerts · current browser session</strong>
            {liquidationAlerts.map((alert) => <div key={alert.id}><span>{alert.kind === 'concentration' ? 'Concentration change' : 'Proximity'}</span><small>{new Date(alert.createdAt).toLocaleTimeString()} · {alert.message}</small></div>)}
          </div>}

        </>}
        {clusterData && <>
          <div className="stats-grid">
            <div><small>Observed events</small><strong>{clusterData.total_events.toLocaleString()}</strong></div>
            <div><small>Detected clusters</small><strong>{clusterData.clusters.length}</strong></div>
            <div><small>Observed notional</small><strong>{Number(clusterData.total_notional).toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong></div>
          </div>
          <div className="cluster-context">
            <div><small>Funding rate · {clusterData.market_context.status}</small><strong>{clusterData.market_context.funding_rate ?? '—'}</strong></div>
            <div><small>Open interest</small><strong>{clusterData.market_context.open_interest ? Number(clusterData.market_context.open_interest).toLocaleString() : '—'}</strong></div>
            <div><small>OI change · 2 recent points</small><strong>{clusterData.market_context.open_interest_change_pct ? Number(clusterData.market_context.open_interest_change_pct).toFixed(2) + '%' : '—'}</strong></div>
            <div><small>Long / Short ratio</small><strong>{clusterData.market_context.long_short_ratio ?? '—'}</strong></div>
          </div>
          <p className="footnote">{clusterData.market_context.note}</p>
          {clusterData.truncated && <p className="footnote">The query reached its event cap; the detected clusters may omit older events in this window.</p>}
          {clusterData.clusters.length ? <div className="cluster-list">
            {clusterData.clusters.map((cluster, index) => <article className="cluster-card" key={cluster.price_from + '-' + cluster.price_to}>
              <div className="cluster-card-head">
                <strong>Cluster {index + 1} · {cluster.dominant_side.toUpperCase()}</strong>
                <span>{Number(cluster.strength_pct).toFixed(1)}% strength</span>
              </div>
              <div className="cluster-price">{Number(cluster.price_from).toLocaleString()} – {Number(cluster.price_to).toLocaleString()}</div>
              <div className="cluster-bar"><span style={{ width: Math.min(100, Number(cluster.strength_pct)) + '%' }} /></div>
              <div className="cluster-stats">
                <span>{cluster.events} events</span>
                <span>Long {Number(cluster.long_notional).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
                <span>Short {Number(cluster.short_notional).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
              </div>
              <small className="muted">Last observed: {new Date(cluster.last_event_time).toLocaleString()}</small>
            </article>)}
          </div> : <div className="empty-state">No price bands met the selected strength and minimum-event filters.</div>}
          <p className="footnote">{clusterData.method} · Context fields are from the configured derivatives venue; no causal relationship is inferred.</p>
        </>}
        {!clusterData && !clusterBusy && !clusterError && <div className="empty-state">Detect clusters from persisted liquidation events. PostgreSQL storage is required.</div>}
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><p className="eyebrow">STRATEGY RESEARCH</p><h2>SMA crossover backtest</h2></div>
          <span className="pill">Long-only · Historical data</span>
        </div>
        <form className="controls backtest-controls" onSubmit={runBacktest}>
          <label>Start (local time)<input type="datetime-local" value={backtestStart} onChange={(event) => setBacktestStart(event.target.value)} required /></label>
          <label>End (local time)<input type="datetime-local" value={backtestEnd} onChange={(event) => setBacktestEnd(event.target.value)} required /></label>
          <label>Fast SMA<input type="number" min="1" max="500" value={fastPeriod} onChange={(event) => setFastPeriod(event.target.value)} required /></label>
          <label>Slow SMA<input type="number" min="2" max="1000" value={slowPeriod} onChange={(event) => setSlowPeriod(event.target.value)} required /></label>
          <button disabled={backtestBusy || busy}>{backtestBusy ? 'Running…' : 'Run backtest'}</button>
        </form>
        <p className="footnote">Signals use closed candles and execute at the next candle open. Default fees and slippage are applied.</p>
        {backtestError && <p className="error" role="alert">{backtestError}</p>}
        {backtestResult && <>
          <div className="stats-grid">
            <div><small>Final equity</small><strong>{Number(backtestResult.result.final_equity).toFixed(2)}</strong></div>
            <div><small>Net return</small><strong>{Number(backtestResult.result.net_return_pct).toFixed(2)}%</strong></div>
            <div><small>Max drawdown*</small><strong>{Number(backtestResult.result.max_drawdown_pct).toFixed(2)}%</strong></div>
            <div><small>Win rate</small><strong>{Number(backtestResult.result.win_rate_pct).toFixed(1)}%</strong></div>
            <div><small>Trades</small><strong>{backtestResult.result.trade_count}</strong></div>
            <div><small>Candles used</small><strong>{backtestResult.candles_used}</strong></div>
            <div><small>Profit factor</small><strong>{backtestResult.result.profit_factor === null ? '—' : Number(backtestResult.result.profit_factor).toFixed(2)}</strong></div>
            <div><small>Expectancy</small><strong>{Number(backtestResult.result.expectancy).toFixed(2)}</strong></div>
            <div><small>Exposure</small><strong>{Number(backtestResult.result.exposure_pct).toFixed(1)}%</strong></div>
          </div>
          <div className="chart-wrap equity-chart">
            <div className="chart-caption">Marked-to-market equity curve</div>
            <svg viewBox="0 0 800 190" role="img" aria-label="Backtest equity curve">
              <line x1="8" y1="22" x2="792" y2="22" />
              <line x1="8" y1="97" x2="792" y2="97" />
              <line x1="8" y1="172" x2="792" y2="172" />
              <polyline points={equityCurvePolyline(backtestResult.result.equity_curve)} />
            </svg>
          </div>
          <div className="table-wrap">
            <table><thead><tr><th>Entry (UTC)</th><th>Exit (UTC)</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Return</th></tr></thead>
              <tbody>{backtestResult.result.trades.map((trade, index) => <tr key={`${trade.entry_time}-${index}`}>
                <td>{new Date(trade.entry_time).toLocaleString('en-US', { timeZone: 'UTC' })}</td>
                <td>{new Date(trade.exit_time).toLocaleString('en-US', { timeZone: 'UTC' })}</td>
                <td>{trade.entry_price}</td><td>{trade.exit_price}</td><td>{Number(trade.pnl).toFixed(2)}</td><td>{Number(trade.return_pct).toFixed(2)}%</td>
              </tr>)}</tbody>
            </table>
          </div>
          <p className="footnote">Drawdown uses candle-close marked-to-market equity with estimated liquidation fees. Exposure is the share of candles spent holding a position. Profit factor is unavailable when there are no losing trades. Provider: {backtestResult.provider}.</p>
        </>}
      </section>
      <footer>ChartAnalyzer Next · Local quantitative research MVP · Market data is informational, not investment advice.</footer>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
