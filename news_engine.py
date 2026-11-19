"""Read-only market-news collection for ChartAnalyzer.

RSS/GDELT data is informational context. This module deliberately does not
convert headlines into trading signals or bullish/bearish recommendations, and
nothing here feeds the research registry, the OOS evidence store or the
deployment gate.

Hardening applied to untrusted network input:
  * XML is parsed with defusedxml (DTD/entity expansion forbidden).
  * Responses are streamed with a hard byte cap instead of buffered unbounded.
  * Redirects are bounded and must end on HTTPS.
  * Text is truncated before any regular expression is applied.
  * Forced refreshes are throttled so the endpoint cannot be used to hammer
    upstream publishers.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlsplit, urlunsplit

import requests
from defusedxml.ElementTree import ParseError
from defusedxml.ElementTree import fromstring as _safe_fromstring

FEEDS = {
    "bbc": {
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "language": "en",
    },
    "cnn": {
        "url": "https://rss.cnn.com/rss/edition_world.rss",
        "language": "en",
    },
    "iran_international": {
        "url": "https://www.iranintl.com/en/feed",
        "language": "en",
    },
    "aljazeera": {
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "language": "en",
    },
    "oilprice": {
        "url": "https://oilprice.com/rss/main",
        "language": "en",
    },
    "coindesk": {
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "language": "en",
    },
}
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

MAX_FEED_BYTES = 3_000_000
MAX_TEXT_INPUT_CHARS = 20_000
MAX_REDIRECTS = 3
MIN_FORCED_REFRESH_SEC = 30.0

_QUERY_ALIASES = {
    "btc": ("btc", "bitcoin"),
    "بیت کوین": ("بیت کوین", "bitcoin", "btc"),
    "eth": ("eth", "ethereum"),
    "اتریوم": ("اتریوم", "ethereum", "eth"),
    "usdt": ("usdt", "tether", "stablecoin"),
    "طلا": ("طلا", "gold"),
    "نفت": ("نفت", "oil", "brent", "wti"),
    "دلار": ("دلار", "usd", "dollar", "forex"),
    "ایران": ("ایران", "iran", "rial", "toman"),
}
_TOPIC_RULES = {
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "blockchain", "binance", "coinbase", "stablecoin", "tether"),
    "iran": ("iran", "iranian", "rial", "toman", "تحریم", "ایران", "ریال", "تومان"),
    "oil_energy": ("oil", "brent", "wti", "opec", "energy", "نفت", "گاز"),
    "macro_fx": ("federal reserve", "fed ", "interest rate", "inflation", "dollar", "usd", "forex", "central bank", "تورم", "بانک مرکزی"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()


def _child_text(node, names: tuple[str, ...]) -> str:
    for child in list(node):
        if _local_name(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return ""


def _clean_html(value: str, maximum: int = 900) -> str:
    # Bound the work done by the regexes below: feed text is untrusted.
    value = (value or "")[:MAX_TEXT_INPUT_CHARS]
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()[:maximum]


def _parse_datetime(value: str) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    parsed = None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            parsed = None
            for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
                try:
                    parsed = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _safe_url(value: str) -> str:
    value = (value or "").strip()
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        if parts.username is not None or parts.password is not None:
            return ""
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return ""


def _topics(text: str) -> list[str]:
    folded = f" {text.casefold()} "
    return [name for name, terms in _TOPIC_RULES.items() if any(t in folded for t in terms)] or ["general"]


def _make_id(source: str, link: str, title: str) -> str:
    raw = (link or f"{source}|{title.casefold()}").encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:20]


def parse_rss(content: bytes, source: str, language: str = "en") -> list[dict]:
    """Parse RSS 2.0, RDF/RSS and Atom feeds. DTDs and entities are rejected."""
    try:
        root = _safe_fromstring(content, forbid_dtd=True)
    except (ParseError, ValueError) as exc:
        # defusedxml's DTD/entity errors subclass ValueError.
        raise ValueError("invalid or unsafe RSS/Atom XML") from exc

    entries = [node for node in root.iter() if _local_name(node.tag) in ("item", "entry")]
    fetched = _utc_now().isoformat()
    output = []
    for entry in entries:
        title = _clean_html(_child_text(entry, ("title",)), 300)
        if not title:
            continue
        summary = _clean_html(_child_text(entry, ("description", "summary", "content", "encoded")), 900)
        link = _safe_url(_child_text(entry, ("link", "guid", "id")))
        if not link:
            for child in list(entry):
                if _local_name(child.tag) == "link" and child.attrib.get("href"):
                    link = _safe_url(child.attrib["href"])
                    if link:
                        break
        date_text = _child_text(entry, ("pubdate", "published", "updated", "date"))
        published = _parse_datetime(date_text)
        body = f"{title} {summary}"
        output.append({
            "id": _make_id(source, link, title),
            "source": source,
            "title": title,
            "summary": summary,
            "url": link,
            "published_utc": published,
            "fetched_utc": fetched,
            "language": language,
            "topics": _topics(body),
        })
    return output


def _read_capped(response, limit: int = MAX_FEED_BYTES) -> bytes:
    """Read a streamed response without ever holding more than `limit` bytes."""
    declared = str(response.headers.get("Content-Length") or "").strip()
    if declared.isdigit() and int(declared) > limit:
        raise ValueError(f"feed response exceeded {limit // 1_000_000} MB")
    chunks, total = [], 0
    for chunk in response.iter_content(chunk_size=65536):
        total += len(chunk)
        if total > limit:
            raise ValueError(f"feed response exceeded {limit // 1_000_000} MB")
        chunks.append(chunk)
    return b"".join(chunks)


class NewsService:
    """Thread-safe, read-only RSS/GDELT collector with independent query caches."""
    def __init__(self):
        self._lock = threading.RLock()
        self._items: list[dict] = []
        self._statuses: dict[str, dict] = {}
        self._refreshed_at = 0.0
        self._last_forced = float("-inf")
        self._last_config_key = None
        self._gdelt_cache: dict[tuple, tuple[float, list[dict], dict]] = {}

    @staticmethod
    def _session(proxy: str | None) -> requests.Session:
        session = requests.Session()
        session.trust_env = False
        session.max_redirects = MAX_REDIRECTS
        session.headers.update({
            "User-Agent": "ChartAnalyzer (+read-only market news)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json, */*",
        })
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        return session

    def _fetch_feed(self, name: str, spec: dict, timeout: float, proxy: str | None) -> tuple[str, list[dict], dict]:
        started = time.monotonic()
        try:
            with (self._session(proxy) as session,
                  closing(session.get(spec["url"], timeout=(3.05, timeout), allow_redirects=True, stream=True)) as response):
                response.raise_for_status()
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("feed redirected to a non-HTTPS URL")
                content = _read_capped(response)
            items = parse_rss(content, name, spec.get("language", "en"))
            return name, items, {"ok": True, "count": len(items), "latency_ms": round((time.monotonic() - started) * 1000)}
        except Exception as exc:
            return name, [], {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}

    def _fetch_gdelt(self, query: str, limit: int, timeout: float, proxy: str | None) -> tuple[list[dict], dict]:
        started = time.monotonic()
        if not query:
            return [], {"ok": False, "count": 0, "skipped": True}
        try:
            with (self._session(proxy) as session,
                  closing(session.get(
                      GDELT_URL,
                      params={"query": query[:180], "format": "json", "maxrecords": min(50, max(1, limit)), "sort": "HybridRel"},
                      timeout=(3.05, timeout), allow_redirects=False, stream=True,
                  )) as response):
                response.raise_for_status()
                payload = _read_json_capped(response)
            rows = payload.get("articles", []) if isinstance(payload, dict) else []
            fetched = _utc_now().isoformat()
            items = []
            for row in rows[:limit]:
                if not isinstance(row, dict):
                    continue
                title = _clean_html(str(row.get("title", "")), 300)
                link = _safe_url(str(row.get("url", "")))
                if not title:
                    continue
                published = _parse_datetime(str(row.get("seendate", "")))
                items.append({
                    "id": _make_id("gdelt", link, title), "source": "gdelt",
                    "title": title, "summary": "", "url": link,
                    "published_utc": published, "fetched_utc": fetched,
                    "language": str(row.get("language", "en"))[:16], "topics": _topics(title),
                })
            return items, {"ok": True, "count": len(items), "latency_ms": round((time.monotonic() - started) * 1000)}
        except Exception as exc:
            return [], {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        raw = query.strip().casefold()
        if not raw:
            return []
        terms = [x.strip() for x in re.split(r"\s+OR\s+|[,;|]", raw) if x.strip()]
        expanded: list[str] = []
        for term in terms:
            expanded.extend(_QUERY_ALIASES.get(term, (term,)))
        return list(dict.fromkeys(expanded))

    @staticmethod
    def _dedupe(items: list[dict]) -> list[dict]:
        seen = set()
        out = []
        for item in items:
            key = item.get("url") or item.get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        out.sort(key=lambda x: x.get("published_utc") or x.get("fetched_utc") or "", reverse=True)
        return out

    def fetch(self, config: dict | None = None, query: str = "", limit: int = 30, refresh: bool = False) -> dict:
        config = config if isinstance(config, dict) else {}
        limit = max(1, min(int(limit), 100))
        query = str(query or "").strip()[:180]
        if config.get("enabled", True) is False:
            return {"ok": True, "enabled": False, "query": query, "count": 0, "items": [], "sources": {},
                    "message": "news collection is disabled", "notice": "Informational context only; no trade signal is produced."}

        try:
            timeout = max(2.0, min(float(config.get("timeout_sec", 8)), 20.0))
        except (TypeError, ValueError):
            timeout = 8.0
        try:
            ttl = max(30, min(int(config.get("cache_ttl_sec", 300)), 3600))
        except (TypeError, ValueError):
            ttl = 300
        proxy = str(config.get("proxy", "") or "").strip() or None
        if proxy:
            parsed = urlsplit(proxy)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                raise ValueError("news.proxy must be an http(s) proxy URL without embedded credentials")
        disabled_raw = config.get("disabled_sources", []) or []
        if not isinstance(disabled_raw, (list, tuple)):
            raise ValueError("news.disabled_sources must be a list")
        disabled = {str(x).strip().lower() for x in disabled_raw}
        selected = {k: v for k, v in FEEDS.items() if k not in disabled}
        unknown = disabled.difference(FEEDS)
        if unknown:
            raise ValueError("unknown news source(s): " + ", ".join(sorted(unknown)))
        config_key = (timeout, ttl, proxy, tuple(sorted(disabled)))

        with self._lock:
            throttled = False
            if refresh:
                if time.monotonic() - self._last_forced < MIN_FORCED_REFRESH_SEC:
                    refresh, throttled = False, True
                else:
                    self._last_forced = time.monotonic()
            rss_stale = (time.monotonic() - self._refreshed_at) >= ttl or config_key != self._last_config_key
            if refresh or rss_stale:
                items: list[dict] = []
                statuses: dict[str, dict] = {}
                if selected:
                    with ThreadPoolExecutor(max_workers=min(6, len(selected))) as pool:
                        futures = [pool.submit(self._fetch_feed, name, spec, timeout, proxy) for name, spec in selected.items()]
                        for future in as_completed(futures):
                            name, rows, status = future.result()
                            statuses[name] = status
                            items.extend(rows)
                self._items = self._dedupe(items)
                self._statuses = statuses
                self._refreshed_at = time.monotonic()
                self._last_config_key = config_key

            terms = self._query_terms(query)
            base_items = self._items
            matching = [x for x in base_items if not terms or any(
                t in (x.get("title", "") + " " + x.get("summary", "")).casefold() for t in terms)]
            extra_items: list[dict] = []
            extra_status = None
            if query and len(matching) < 5:
                gdelt_key = (query.casefold(), timeout, proxy)
                cached = self._gdelt_cache.get(gdelt_key)
                gdelt_stale = cached is None or (time.monotonic() - cached[0]) >= ttl
                if refresh or gdelt_stale:
                    rows, status = self._fetch_gdelt(query, min(max(limit, 10), 30), timeout, proxy)
                    self._gdelt_cache[gdelt_key] = (time.monotonic(), rows, status)
                    # Bound memory: one entry per distinct query text.
                    if len(self._gdelt_cache) > 64:
                        oldest = min(self._gdelt_cache, key=lambda k: self._gdelt_cache[k][0])
                        self._gdelt_cache.pop(oldest, None)
                    cached = self._gdelt_cache.get(gdelt_key)
                if cached:
                    extra_items = cached[1]
                    extra_status = cached[2]
            combined = self._dedupe(matching + extra_items)
            statuses = dict(self._statuses)
            if extra_status is not None:
                statuses["gdelt"] = dict(extra_status)
            selected_items = combined[:limit]
            return {
                "ok": True, "enabled": True, "query": query, "count": len(selected_items),
                "total_available": len(combined), "generated_at": _utc_now().isoformat(),
                "cache_age_sec": max(0, int(time.monotonic() - self._refreshed_at)),
                "refresh_throttled": throttled,
                "sources": statuses, "items": [dict(x) for x in selected_items],
                "notice": "News is informational context only; no validated bullish/bearish score or trade signal is produced.",
            }


def _read_json_capped(response, limit: int = MAX_FEED_BYTES):
    return json.loads(_read_capped(response, limit).decode("utf-8", "replace"))


NEWS_SERVICE = NewsService()
