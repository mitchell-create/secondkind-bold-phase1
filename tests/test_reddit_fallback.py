import pytest

import strategy.exa_research as exa_research
import strategy.reddit_research as reddit_research
from strategy.exa_queries import ExaQuery
from strategy.exa_research import ExaQueryResult, run_query
from strategy.reddit_research import RedditAuthError, _hit_from_post

EXA_REDDIT_REFUSAL = ValueError(
    'Request failed with status code 403: {"error":"The following requested '
    'domains are not available: reddit.com...","tag":"SOURCE_NOT_AVAILABLE"}'
)


class _RefusingExa:
    def search_and_contents(self, *args, **kwargs):
        raise EXA_REDDIT_REFUSAL


def _reddit_query() -> ExaQuery:
    return ExaQuery(
        label="reddit-zoka-coffee-honest",
        query="Zoka Coffee honest review",
        include_domains=["reddit.com"],
        category="reddit",
    )


def test_reddit_scoped_refusal_falls_back_to_reddit_api(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _RefusingExa())
    sentinel = ExaQueryResult(query=_reddit_query(), fetched_at="t", results=[])
    monkeypatch.setattr(
        reddit_research, "run_reddit_query",
        lambda query, content_chars=3000: sentinel,
    )

    assert run_query(_reddit_query()) is sentinel


def test_fallback_auth_failure_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _RefusingExa())

    def no_creds(query, content_chars=3000):
        raise RedditAuthError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set.")

    monkeypatch.setattr(reddit_research, "run_reddit_query", no_creds)

    with pytest.raises(RuntimeError) as exc:
        run_query(_reddit_query())
    message = str(exc.value)
    assert "Exa no longer serves reddit.com" in message
    assert "REDDIT_CLIENT_ID" in message


def test_non_reddit_errors_propagate_untouched(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _RefusingExa())

    def explode(*args, **kwargs):
        raise AssertionError("fallback must not run for non-reddit queries")

    monkeypatch.setattr(reddit_research, "run_reddit_query", explode)

    plain = ExaQuery(label="web-zoka-coffee-love", query="zoka love")
    with pytest.raises(ValueError):
        run_query(plain)


def test_hit_from_post_shaping():
    post = {
        "permalink": "/r/espresso/comments/abc/zoka_paladino/",
        "title": "Zoka Paladino — worth it?",
        "author": "grinder_guy",
        "score": 42,
        "created_utc": 1750000000,
        "selftext": "Thinking of switching from my usual roaster.",
    }
    hit = _hit_from_post(post, ["It's my daily driver.", "Too dark for me."], 3000)
    assert hit.url == "https://www.reddit.com/r/espresso/comments/abc/zoka_paladino/"
    assert hit.domain == "reddit.com"
    assert hit.score == 42.0
    assert "TOP COMMENTS:" in hit.text
    assert "daily driver" in hit.text
    assert hit.published_date and hit.published_date.startswith("2025")


def test_hit_from_post_respects_content_budget():
    post = {"permalink": "/r/coffee/comments/x/", "title": "t", "selftext": "a" * 5000}
    hit = _hit_from_post(post, [], 3000)
    assert len(hit.text) == 3000


# ─── Empty-result path ───────────────────────────────────────────────────────
# Exa stopped raising SOURCE_NOT_AVAILABLE for reddit.com and now returns a
# successful response with zero results. The fallback chain used to live only
# in the exception handler, so every reddit-scoped query silently cached an
# empty result and no tier ever ran (found on the OnCore Longevity run,
# 2026-09-08: 13/13 reddit queries returned 0 with no error record).

from types import SimpleNamespace

import strategy.apify_reddit as apify_reddit
from strategy.apify_reddit import ApifyRedditError


class _EmptyExa:
    def search_and_contents(self, *args, **kwargs):
        return SimpleNamespace(results=[])


def test_reddit_scoped_empty_result_falls_back_to_reddit_api(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _EmptyExa())
    sentinel = ExaQueryResult(query=_reddit_query(), fetched_at="t", results=[])
    monkeypatch.setattr(
        reddit_research, "run_reddit_query",
        lambda query, content_chars=3000: sentinel,
    )

    assert run_query(_reddit_query()) is sentinel


def test_reddit_scoped_empty_result_reaches_apify_when_api_unusable(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _EmptyExa())

    def no_creds(query, content_chars=3000):
        raise RedditAuthError("REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set.")

    monkeypatch.setattr(reddit_research, "run_reddit_query", no_creds)
    sentinel = ExaQueryResult(query=_reddit_query(), fetched_at="t", results=[])
    monkeypatch.setattr(
        apify_reddit, "run_reddit_query_via_apify",
        lambda query, content_chars=3000: sentinel,
    )

    assert run_query(_reddit_query()) is sentinel


def test_reddit_scoped_empty_result_all_tiers_failing_raises_combined_error(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _EmptyExa())

    def no_creds(query, content_chars=3000):
        raise RedditAuthError("creds pending")

    def no_token(query, content_chars=3000):
        raise ApifyRedditError("APIFY_API_TOKEN not set")

    monkeypatch.setattr(reddit_research, "run_reddit_query", no_creds)
    monkeypatch.setattr(apify_reddit, "run_reddit_query_via_apify", no_token)

    with pytest.raises(RuntimeError) as exc:
        run_query(_reddit_query())
    message = str(exc.value)
    assert "Exa no longer serves reddit.com" in message
    assert "creds pending" in message
    assert "APIFY_API_TOKEN" in message


def test_non_reddit_empty_result_is_returned_without_fallback(monkeypatch):
    monkeypatch.setattr(exa_research, "_get_client", lambda: _EmptyExa())

    def explode(*args, **kwargs):
        raise AssertionError("fallback must not run for non-reddit queries")

    monkeypatch.setattr(reddit_research, "run_reddit_query", explode)
    monkeypatch.setattr(apify_reddit, "run_reddit_query_via_apify", explode)

    plain = ExaQuery(label="web-zoka-coffee-love", query="zoka love")
    result = run_query(plain)
    assert result.query is plain
    assert result.results == []
