import pytest

import news_engine as NE


def test_parse_rss_cleans_fields_and_parses_dates():
    xml = b"""<?xml version='1.0'?><rss version='2.0'><channel><item>
      <title>Bitcoin &amp; Markets</title>
      <description><![CDATA[<b>Market</b> update]]></description>
      <link>https://example.com/story?x=1</link>
      <pubDate>Tue, 18 Nov 2025 10:00:00 GMT</pubDate>
    </item></channel></rss>"""
    rows = NE.parse_rss(xml, "test", "en")
    assert len(rows) == 1
    assert rows[0]["title"] == "Bitcoin & Markets"
    assert rows[0]["summary"] == "Market update"
    assert rows[0]["url"] == "https://example.com/story?x=1"
    assert rows[0]["published_utc"].startswith("2025-11-18T10:00:00")
    assert "crypto" in rows[0]["topics"]


def test_parse_rss_rejects_dtd_and_entity_expansion():
    bomb = (b"<?xml version='1.0'?><!DOCTYPE lolz [<!ENTITY a 'aaaaaaaaaa'>"
            b"<!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;'>]>"
            b"<rss><channel><item><title>&b;</title></item></channel></rss>")
    with pytest.raises(ValueError, match="unsafe"):
        NE.parse_rss(bomb, "test")


def test_parse_rss_rejects_external_entity():
    xxe = (b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]>"
           b"<rss><channel><item><title>&e;</title></item></channel></rss>")
    with pytest.raises(ValueError, match="unsafe"):
        NE.parse_rss(xxe, "test")


def test_parse_rss_rejects_malformed_xml():
    with pytest.raises(ValueError):
        NE.parse_rss(b"<rss><channel><item>", "test")


def test_safe_url_rejects_non_http_credentials_and_fragment():
    assert NE._safe_url("javascript:alert(1)") == ""
    assert NE._safe_url("https://user:pass@example.com/a") == ""
    assert NE._safe_url("https://example.com/a#private") == "https://example.com/a"


def test_clean_html_strips_script_and_bounds_input():
    cleaned = NE._clean_html("<script>alert(1)</script><b>ok</b> " + "x" * 100_000, maximum=50)
    assert "alert" not in cleaned
    assert cleaned.startswith("ok")
    assert len(cleaned) <= 50


class _FakeResponse:
    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    def iter_content(self, chunk_size=1):
        yield from self._chunks


def test_read_capped_enforces_byte_limit_while_streaming():
    resp = _FakeResponse([b"a" * 600, b"b" * 600])
    with pytest.raises(ValueError, match="exceeded"):
        NE._read_capped(resp, limit=1000)
    assert NE._read_capped(_FakeResponse([b"abc", b"def"]), limit=1000) == b"abcdef"


def test_read_capped_rejects_declared_oversize_before_reading():
    resp = _FakeResponse([b"x"], headers={"Content-Length": "999999999"})
    with pytest.raises(ValueError, match="exceeded"):
        NE._read_capped(resp, limit=1000)


def test_gdelt_cache_is_separated_by_query(monkeypatch):
    service = NE.NewsService()
    monkeypatch.setattr(service, "_fetch_feed", lambda name, *_: (name, [], {"ok": True, "count": 0}))
    calls = []

    def gdelt(query, *_args):
        calls.append(query)
        title = "Bitcoin market report" if "btc" in query.lower() else "Ethereum market report"
        item = {"id": title, "source": "gdelt", "title": title, "summary": "",
                "url": "https://example.com/" + title.split()[0].lower(),
                "published_utc": "2026-01-01T00:00:00+00:00", "fetched_utc": "2026-01-01T00:00:00+00:00",
                "language": "en", "topics": ["crypto"]}
        return [item], {"ok": True, "count": 1}

    monkeypatch.setattr(service, "_fetch_gdelt", gdelt)
    cfg = {"enabled": True, "cache_ttl_sec": 300, "disabled_sources": []}
    first = service.fetch(cfg, query="btc")
    again = service.fetch(cfg, query="btc")
    other = service.fetch(cfg, query="eth")

    assert first["items"][0]["title"] == "Bitcoin market report"
    assert again["items"][0]["title"] == "Bitcoin market report"
    assert other["items"][0]["title"] == "Ethereum market report"
    assert len(calls) == 2


def test_forced_refresh_is_throttled(monkeypatch):
    service = NE.NewsService()
    feed_calls = []

    def fake_feed(name, *_):
        feed_calls.append(name)
        return name, [], {"ok": True, "count": 0}

    monkeypatch.setattr(service, "_fetch_feed", fake_feed)
    cfg = {"enabled": True, "cache_ttl_sec": 3600, "disabled_sources": []}
    first = service.fetch(cfg, refresh=True)
    n_after_first = len(feed_calls)
    second = service.fetch(cfg, refresh=True)

    assert first["refresh_throttled"] is False
    assert second["refresh_throttled"] is True
    assert len(feed_calls) == n_after_first  # second forced refresh served from cache


def test_invalid_source_config_fails_closed():
    service = NE.NewsService()
    with pytest.raises(ValueError, match="disabled_sources"):
        service.fetch({"disabled_sources": "bbc"})
    with pytest.raises(ValueError, match="unknown news source"):
        service.fetch({"disabled_sources": ["nonexistent"]})


def test_proxy_with_credentials_is_rejected():
    service = NE.NewsService()
    with pytest.raises(ValueError, match="proxy"):
        service.fetch({"proxy": "http://user:pass@proxy.example:3128"})


def test_news_can_be_disabled_without_network_calls():
    service = NE.NewsService()
    result = service.fetch({"enabled": False}, query="bitcoin")
    assert result["ok"] is True
    assert result["enabled"] is False
    assert result["items"] == []


def test_parse_gdelt_compact_timestamp():
    assert NE._parse_datetime("20261119T142500Z") == "2026-11-19T14:25:00+00:00"
