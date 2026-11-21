import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react';
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
