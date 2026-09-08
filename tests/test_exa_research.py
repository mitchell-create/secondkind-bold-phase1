import json

import strategy.exa_research as exa_research
from strategy.exa_research import (
    ExaHit,
    ExaQuery,
    ExaQueryResult,
    cache_error,
    cache_result,
    load_cached,
)


def test_cache_error_persists_failure(tmp_path, monkeypatch):
    """A failed Exa query must leave a readable error record — before this
    existed, failures vanished after the run (Zoka issue #8)."""
    monkeypatch.setattr(exa_research, "CLIENTS_DIR", tmp_path / "clients")

    query = ExaQuery(label="reddit-zoka-coffee-honest", query="zoka honest review")
    path = cache_error("zoka-coffee", query, ValueError("boom"))

    assert path.exists()
    assert path.parent.name == "errors"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["error_type"] == "ValueError"
    assert payload["error"] == "boom"
    assert payload["query"]["label"] == "reddit-zoka-coffee-honest"


def test_cache_result_clears_stale_error_record(tmp_path, monkeypatch):
    """A query that failed once and later succeeded must not keep reporting
    a failure ghost in status."""
    monkeypatch.setattr(exa_research, "CLIENTS_DIR", tmp_path / "clients")

    query = ExaQuery(label="reddit-zoka-coffee-honest", query="zoka honest review")
    error_path = cache_error("zoka-coffee", query, ValueError("boom"))
    assert error_path.exists()

    cache_result("zoka-coffee", ExaQueryResult(
        query=query, fetched_at="2026-07-05T00:00:00Z", results=[],
    ))
    assert not error_path.exists()


def test_cache_result_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(exa_research, "CLIENTS_DIR", tmp_path / "clients")

    result = ExaQueryResult(
        query=ExaQuery(label="web-zoka-coffee-love", query="zoka love"),
        fetched_at="2026-07-05T00:00:00Z",
        results=[ExaHit(
            url="https://example.com/a",
            title="A",
            published_date=None,
            author=None,
            score=0.5,
            text="great coffee",
            domain="example.com",
        )],
    )
    path = cache_result("zoka-coffee", result)
    assert path.stem == "web-zoka-coffee-love"

    loaded = load_cached("zoka-coffee")
    assert len(loaded) == 1
    assert loaded[0].query.label == "web-zoka-coffee-love"
    assert loaded[0].results[0].domain == "example.com"


def test_run_research_bundle_uses_explicit_queries_and_records_failures(tmp_path, monkeypatch):
    """A single failing query must not abort the bundle: it lands under
    research/exa/errors/ (rule 6 diagnostics) and the run continues."""
    monkeypatch.setattr(exa_research, "CLIENTS_DIR", tmp_path / "clients")

    good = ExaQuery(label="web-zoka-coffee-concerns", query="zoka concerns")
    bad = ExaQuery(label="reddit-zoka-coffee-honest", query="zoka honest",
                   include_domains=["reddit.com"], category="reddit")

    def fake_run_query(query, content_chars=3000, livecrawl=None):
        if query is bad:
            raise RuntimeError("all tiers down")
        return ExaQueryResult(query=query, fetched_at="t", results=[ExaHit(
            url="https://example.com/x", title="x", published_date=None,
            author=None, score=None, text="t", domain="example.com",
        )])

    monkeypatch.setattr(exa_research, "run_query", fake_run_query)

    results = exa_research.run_research_bundle(
        "zoka-coffee", "Zoka Coffee", queries=[good, bad],
    )

    assert [r.query.label for r in results] == [good.label, bad.label]
    assert results[0].error is None and len(results[0].results) == 1
    assert results[1].error == "all tiers down" and results[1].results == []

    raw_dir = tmp_path / "clients" / "zoka-coffee" / "research" / "exa" / "raw"
    err_dir = tmp_path / "clients" / "zoka-coffee" / "research" / "exa" / "errors"
    assert (raw_dir / "web-zoka-coffee-concerns.json").exists()
    assert not (raw_dir / "reddit-zoka-coffee-honest.json").exists()
    assert (err_dir / "reddit-zoka-coffee-honest.json").exists()


def test_run_research_bundle_default_queries_have_no_consumable_assumptions():
    """The old starter set hardcoded 'taste' and 'ingredients' queries for every
    brand; for a gym they returned SEC filings and supplement listicles."""
    from strategy.exa_queries import default_queries_for_brand

    queries = default_queries_for_brand("OnCore Longevity", ["GoodLife Fitness"])
    joined = " ".join(f"{q.label} {q.query}" for q in queries).lower()
    assert "taste" not in joined
    assert "ingredient" not in joined


def test_write_reddit_voc_dump_mirrors_reddit_hits_for_mine_voc(tmp_path, monkeypatch):
    monkeypatch.setattr(exa_research, "CLIENTS_DIR", tmp_path / "clients")
    reddit_q = ExaQuery(label="reddit-category-over-50", query="x",
                        include_domains=["reddit.com"], category="category-discussion")
    web_q = ExaQuery(label="web-goodlife-honest", query="y")
    hit = lambda url, text: ExaHit(url=url, title="t", published_date=None,
                                   author=None, score=None, text=text, domain="reddit.com")
    results = [
        ExaQueryResult(query=reddit_q, fetched_at="t", results=[
            hit("https://www.reddit.com/r/a/comments/1/", "we don't bounce back in our 40s"),
            hit("https://www.reddit.com/r/a/comments/1/", "duplicate url"),
            hit("https://www.reddit.com/r/a/comments/2/", "   "),
        ]),
        ExaQueryResult(query=web_q, fetched_at="t", results=[hit("https://example.com", "web")]),
    ]

    path, n = exa_research.write_reddit_voc_dump("oncore", results)
    assert n == 1 and path.name == "reddit-threads.json" and path.parent.name == "voc"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert rows[0]["body"] == "we don't bounce back in our 40s"
    assert rows[0]["rating"] is None
    assert exa_research.write_reddit_voc_dump("oncore", [results[1]]) == (None, 0)
