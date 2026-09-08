"""Business-specific Exa query planning.

The starter query set used to be hardcoded for consumables (taste, ingredients,
'best X recommendation'). The plan is generated per client from its brand
context, cached as a reviewable YAML, and turned into ExaQuery objects here.
"""

import textwrap

import pytest
import yaml

import strategy.exa_query_plan as qp
from strategy.exa_query_plan import (
    QueryPlan,
    QueryPlanError,
    generate_query_plan,
    load_query_plan,
    plan_from_dict,
    queries_from_plan,
    query_plan_path,
    save_query_plan,
)

PLAN_YAML = textwrap.dedent("""\
    business_type: membership fitness and longevity center (local service, pre-launch)
    brand_footprint: pre_launch
    rationale: No members yet, so brand-name searches return nothing useful; VOC lives in category and competitor conversations.
    brand_angles:
      - label: concerns
        query: "{brand} concerns complaints problems"
        category: reviews
    competitor_angles:
      - label: honest
        query: "{competitor} honest review worth it"
        category: reviews
      - label: quit
        query: "why I quit {competitor} injury too intense"
        category: reddit
        reddit: true
    category_terms:
      - strength training over 50
      - gym intimidation beginner
    """)


def _plan(**overrides) -> QueryPlan:
    data = yaml.safe_load(PLAN_YAML)
    data.update(overrides)
    return plan_from_dict(data)


def test_plan_from_dict_rejects_bad_footprint_and_missing_placeholder():
    with pytest.raises(QueryPlanError):
        _plan(brand_footprint="huge")
    with pytest.raises(QueryPlanError):
        _plan(competitor_angles=[{"label": "honest", "query": "honest review"}])
    with pytest.raises(QueryPlanError):
        _plan(category_terms=[])
    with pytest.raises(QueryPlanError):
        _plan(brand_angles=[{"label": "concerns", "query": "concerns complaints"}])


def test_queries_from_plan_pre_launch_skips_brand_name_queries():
    queries = queries_from_plan(_plan(), "OnCore Longevity", ["GoodLife Fitness"])
    labels = [q.label for q in queries]

    assert not any(l.startswith("reddit-oncore-longevity") for l in labels)
    assert not any(l.startswith("web-oncore-longevity") for l in labels)
    assert "web-goodlife-fitness-honest" in labels
    assert "reddit-goodlife-fitness-quit" in labels
    assert "reddit-category-strength-training-over-50" in labels
    assert "reddit-category-gym-intimidation-beginner" in labels


def test_queries_from_plan_established_brand_includes_brand_queries():
    queries = queries_from_plan(
        _plan(brand_footprint="established"), "Zoka Coffee", ["Stumptown"],
    )
    by_label = {q.label: q for q in queries}

    assert by_label["reddit-zoka-coffee-honest"].keyword_query == '"Zoka Coffee" review'
    assert "reddit-zoka-coffee-worth-it" in by_label
    assert by_label["web-zoka-coffee-concerns"].query == "Zoka Coffee concerns complaints problems"
    assert by_label["reddit-zoka-coffee-vs-stumptown"].category == "comparison"


def test_queries_from_plan_reddit_competitor_angles_carry_keyword_filter():
    queries = queries_from_plan(_plan(), "OnCore Longevity", ["GoodLife Fitness"])
    quit_q = next(q for q in queries if q.label == "reddit-goodlife-fitness-quit")

    assert quit_q.include_domains == ["reddit.com"]
    assert '"GoodLife Fitness"' in quit_q.keyword_query  # must-contain filter for keyword engines
    assert "{competitor}" not in quit_q.query


def test_queries_from_plan_merges_operator_category_terms_without_duplicates():
    queries = queries_from_plan(
        _plan(), "OnCore Longevity", [],
        extra_category_terms=["gym intimidation beginner", "menopause weight gain"],
    )
    cat = [q.label for q in queries if q.category == "category-discussion"]
    assert cat == [
        "reddit-category-strength-training-over-50",
        "reddit-category-gym-intimidation-beginner",
        "reddit-category-menopause-weight-gain",
    ]


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(qp, "CLIENTS_DIR", tmp_path / "clients")
    plan = _plan()
    path = save_query_plan("oncore-longevity", plan)

    assert path == query_plan_path("oncore-longevity")
    assert path.parent.name == "exa"
    loaded = load_query_plan("oncore-longevity")
    assert loaded is not None
    assert loaded.brand_footprint == "pre_launch"
    assert [a.label for a in loaded.competitor_angles] == ["honest", "quit"]
    assert load_query_plan("nobody") is None


def _seed_client(root):
    client = root / "clients" / "oncore-longevity"
    (client / "products").mkdir(parents=True)
    (client / "brand.yaml").write_text(
        "name: OnCore Longevity\ntone: clinical and calm\n"
        "audience:\n  age_range: 45-70\n  gender: mixed\n",
        encoding="utf-8",
    )
    (client / "brand-context.md").write_text(
        "# Brand Context\n\nPre-launch longevity center in Regina using EGYM machines.",
        encoding="utf-8",
    )
    (client / "products" / "membership.yaml").write_text(
        "name: Membership\ndescription: One set, twice a week.\ncategory: fitness\n"
        "benefits:\n  - measured strength\nprice: $99/month\n",
        encoding="utf-8",
    )
    (client / "competitors.yaml").write_text(
        "competitors:\n  - name: GoodLife Fitness\n    type: direct\n    notes: big box\n",
        encoding="utf-8",
    )
    return client


def test_generate_query_plan_uses_injected_completion_and_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(qp, "CLIENTS_DIR", tmp_path / "clients")
    _seed_client(tmp_path)
    seen: dict = {}

    def fake_complete(prompt, system="", max_tokens=4096, **kwargs):
        seen["prompt"] = prompt
        seen["system"] = system
        return "```yaml\n" + PLAN_YAML + "```"

    plan = generate_query_plan("oncore-longevity", complete_fn=fake_complete)

    assert plan.provenance == "llm"
    assert plan.generated_at
    assert plan.business_type.startswith("membership fitness")
    assert "EGYM" in seen["prompt"]            # brand-context.md reached the model
    assert "GoodLife Fitness" in seen["prompt"]  # competitors.yaml reached the model
    assert "One set, twice a week" in seen["prompt"]  # product context reached the model
    assert query_plan_path("oncore-longevity").exists()


def test_generate_query_plan_repairs_broken_yaml_once(tmp_path, monkeypatch):
    monkeypatch.setattr(qp, "CLIENTS_DIR", tmp_path / "clients")
    _seed_client(tmp_path)
    calls = []

    def flaky_complete(prompt, system="", max_tokens=4096, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return "business_type: [unclosed\nbrand_footprint: pre_launch\n"
        return PLAN_YAML

    plan = generate_query_plan("oncore-longevity", complete_fn=flaky_complete)
    assert len(calls) == 2
    assert plan.brand_footprint == "pre_launch"


def test_generate_query_plan_raises_when_model_output_is_unusable(tmp_path, monkeypatch):
    monkeypatch.setattr(qp, "CLIENTS_DIR", tmp_path / "clients")
    _seed_client(tmp_path)

    def bad_complete(prompt, system="", max_tokens=4096, **kwargs):
        return "not: [valid"

    with pytest.raises(QueryPlanError):
        generate_query_plan("oncore-longevity", complete_fn=bad_complete)
    assert not query_plan_path("oncore-longevity").exists()


# ─── Category anchors ────────────────────────────────────────────────────────
# Reddit search pads weak matches with trending junk (a GTA post under "too old
# for the gym", fabric threads under "losing muscle after 50" on the OnCore run,
# 14 of 57 threads). Competitor queries were clean because their quoted name
# becomes the must-contain filter; category terms had no filter. Each term now
# carries an anchor word that any on-topic thread must mention.

def test_plan_accepts_anchored_category_terms():
    plan = _plan(category_terms=[
        {"term": "losing muscle after 50", "anchor": "muscle"},
        "gym intimidating over 60",
    ])
    assert plan.category_terms == ["losing muscle after 50", "gym intimidating over 60"]
    assert plan.category_anchors == {"losing muscle after 50": "muscle"}


def test_anchored_category_query_carries_quoted_must_contain_filter():
    from strategy.exa_queries import reddit_search_terms

    plan = _plan(category_terms=[{"term": "losing muscle after 50", "anchor": "muscle"}])
    q = next(x for x in queries_from_plan(plan, "OnCore Longevity", [])
             if x.category == "category-discussion")
    search, must_contain = reddit_search_terms(q)
    assert must_contain == "muscle"
    assert "losing muscle after 50" in search


def test_unanchored_category_query_has_no_filter():
    from strategy.exa_queries import reddit_search_terms

    plan = _plan(category_terms=["gym intimidating over 60"])
    q = next(x for x in queries_from_plan(plan, "OnCore Longevity", [])
             if x.category == "category-discussion")
    assert reddit_search_terms(q) == ("gym intimidating over 60", "")


def test_anchors_survive_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr(qp, "CLIENTS_DIR", tmp_path / "clients")
    plan = _plan(category_terms=[{"term": "sarcopenia how to stop", "anchor": "sarcopenia"}])
    save_query_plan("x", plan)
    loaded = load_query_plan("x")
    assert loaded.category_anchors == {"sarcopenia how to stop": "sarcopenia"}
