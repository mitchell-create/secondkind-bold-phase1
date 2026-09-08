"""Deterministic copy rules for generated briefs (validators/copy_rules.py).

Each rule has a positive and a negative case drawn from real OnCore Longevity
output on 2026-09-08: 15-20 em-dashes per brief, an invented member cohort for
a pre-launch brand, a second-person age callout, and a competitor named in
visual direction.
"""

from __future__ import annotations

from models.brief import AwarenessLevel, CopyFramework, CreativeBrief, TextOverlay
from validators.copy_rules import (
    RULE_COMPETITOR,
    RULE_EM_DASH,
    RULE_FABRICATED_PROOF,
    RULE_NEEDS_PROOF,
    RULE_PERSONAL_ATTRIBUTE,
    RULE_PROHIBITED_TERM,
    check_brief_copy,
    customer_facing_text,
    flag_strings,
)


def _brief(**overrides) -> CreativeBrief:
    fields = dict(
        brief_id="copy-rules-test",
        client="oncore-longevity",
        product="OnCore Longevity Membership",
        awareness_level=AwarenessLevel.PROBLEM_AWARE,
        framework=CopyFramework.PAS,
        angle="any angle",
        hook="Most gyms count your classes. This one measures your strength.",
        cta="Book your free assessment",
    )
    fields.update(overrides)
    return CreativeBrief(**fields)


def _rules(brief: CreativeBrief, **kwargs) -> set[str]:
    return {v.rule for v in check_brief_copy(brief, **kwargs)}


def test_clean_brief_has_no_violations():
    assert check_brief_copy(_brief(), competitor_names=["Orangetheory Fitness"],
                            social_proof_available=False) == []


def test_em_dash_flagged_in_any_customer_facing_field():
    brief = _brief(hook="A plan that fits — finally.", benefit_callouts=["One set – twice a week"])
    violations = check_brief_copy(brief)
    assert {(v.field, v.rule) for v in violations} == {
        ("hook", RULE_EM_DASH), ("benefit_callouts[0]", RULE_EM_DASH),
    }


def test_competitor_name_flagged_in_visual_direction_but_not_persona_traits():
    brief = _brief(
        visual_direction="The opposite of Orangetheory: one person, one machine.",
        persona_traits="48-58 woman who walked into a GoodLife and left.",
    )
    violations = check_brief_copy(brief, competitor_names=["Orangetheory Fitness", "GoodLife Fitness", "Orangetheory", "GoodLife"])
    assert [(v.field, v.rule) for v in violations] == [("visual_direction", RULE_COMPETITOR)]


def test_second_person_age_and_condition_callouts_flagged():
    assert RULE_PERSONAL_ATTRIBUTE in _rules(_brief(hook="If you're over 45 and the gym stopped working, read this."))
    assert RULE_PERSONAL_ATTRIBUTE in _rules(_brief(hook="If you are 45 or older, your body is not the problem."))
    assert RULE_PERSONAL_ATTRIBUTE in _rules(_brief(body_copy="You have menopause, not a motivation problem."))
    # Third person and category framing are fine.
    assert RULE_PERSONAL_ATTRIBUTE not in _rules(_brief(hook="Every gym was built for a 28-year-old. Women in their fifties noticed."))
    assert RULE_PERSONAL_ATTRIBUTE not in _rules(_brief(hook="She was 54. She had tried the gym twice."))


def test_social_proof_claims_flagged_only_when_brand_has_none():
    brief = _brief(hook="The women in OnCore's 12-week cohort who moved fastest saw the clearest gains.")
    assert RULE_FABRICATED_PROOF in _rules(brief, social_proof_available=False)
    assert RULE_FABRICATED_PROOF not in _rules(brief, social_proof_available=True)
    assert RULE_FABRICATED_PROOF in _rules(_brief(cta="Join 1,200+ members"), social_proof_available=False)
    assert RULE_FABRICATED_PROOF in _rules(_brief(hook="Clients who joined in January lost the shuffle."), social_proof_available=False)
    # Plain narration about people is not a results claim (false positive on the OnCore run).
    assert RULE_FABRICATED_PROOF not in _rules(
        _brief(hook="A gym staffed by people who have never explained why your body changed."),
        social_proof_available=False,
    )


def test_statistics_need_proof_but_product_facts_do_not():
    assert RULE_NEEDS_PROOF in _rules(_brief(hook="After 40, women lose up to 8% of muscle per decade."))
    assert RULE_NEEDS_PROOF in _rules(_brief(hook="Most women in their early 50s have a BioAge 6 to 8 years older than their age."))
    assert RULE_NEEDS_PROOF not in _rules(_brief(hook="One set. Twice a week. Twenty minutes. From $99 a month, 12 weeks, guaranteed."))


def test_prohibited_terms_flagged():
    assert RULE_PROHIBITED_TERM in _rules(_brief(hook="A revolutionary way to age."), prohibited_terms=["revolutionary"])


def test_text_layout_captions_are_customer_facing():
    brief = _brief(text_layout=[TextOverlay(text="Still sore — three days later", y_pct=0.1, font_size_pct=0.05, kind="pill")])
    fields = dict(customer_facing_text(brief))
    assert "text_layout[0]" in fields
    assert ("text_layout[0]", RULE_EM_DASH) in {(v.field, v.rule) for v in check_brief_copy(brief)}


def test_flag_strings_are_readable_and_dash_free():
    flags = flag_strings(check_brief_copy(_brief(hook="Sore — still")))
    assert flags == ["em_dash in hook: 1 dash(es)"]
    assert all("—" not in f for f in flags)
