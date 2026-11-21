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
  }, [authenticated, loadCandles]);

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
      <footer>ChartAnalyzer Next · Local quantitative research MVP · Market data is informational, not investment advice.</footer>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
