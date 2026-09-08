"""Deterministic copy rules for generated briefs.

The angle prompt tells the model the hard rules (see
strategy.angle_multiplier.build_copy_rules_block); this module is the check
that runs on what actually came back. Both exist because the model ignores
prose rules often enough to matter: on the OnCore Longevity run every brief
carried 15-20 em-dashes, one invented a member cohort for a pre-launch brand,
one addressed the reader by age, and one named a competitor in visual
direction.

Checks are deliberately regex-level and non-blocking: they attach flags to the
brief so the operator sees exactly which field broke which rule. `adc brief
--strict` drops flagged briefs instead of saving them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from models.brief import CreativeBrief

# Fields a viewer can see. persona_traits, hook_source, source_insight and the
# trending rationale are strategist notes and may reference competitors.
CUSTOMER_FACING_FIELDS = ("hook", "body_copy", "benefit_callouts", "cta", "visual_direction")

RULE_EM_DASH = "em_dash"
RULE_COMPETITOR = "competitor_named"
RULE_PERSONAL_ATTRIBUTE = "personal_attribute"
RULE_FABRICATED_PROOF = "fabricated_proof"
RULE_NEEDS_PROOF = "needs_proof"
RULE_PROHIBITED_TERM = "prohibited_term"

_DASHES = ("—", "–")

# Second-person age or health-condition callouts. Meta's personal attributes
# policy rejects copy that implies knowledge of the viewer's age or medical
# condition; third-person and category framing is fine.
_PERSONAL_ATTRIBUTE = re.compile(
    r"\b(?:"
    r"(?:if\s+)?you(?:'re|\s+are)\s+(?:over|under|past|in\s+your|already)?\s*\d{2}\b"
    r"|are\s+you\s+(?:over\s+)?\d{2}\b"
    r"|you(?:'re|\s+are)\s+\d{2}\s+or\s+older"
    r"|you\s+(?:have|suffer\s+from|struggle\s+with|are\s+going\s+through)\s+"
    r"(?:menopause|perimenopause|arthritis|osteoporosis|diabetes|sarcopenia|obesity|depression)"
    r"|your\s+(?:menopause|perimenopause|diagnosis|condition)\b"
    r")",
    re.IGNORECASE,
)

# Claims that only make sense if customers exist.
_SOCIAL_PROOF = re.compile(
    r"(?:"
    # "our members", "the cohort", "OnCore's 12-week cohort", "our first clients"
    r"(?:\b(?:our|the)|'s)\s+(?:[\w-]+\s+){0,2}(?:members?|clients?|customers?|cohort|community)\b"
    # "1,200+ members", "300 women"
    r"|\b\d[\d,]*\+?\s+(?:members|clients|customers|women|men|people|reviews)\b"
    # "cohort who moved fastest saw", "women that reported", "clients who lost".
    # Result verbs only: "people who have never explained" is not proof.
    r"|\b(?:members|clients|customers|cohort|women|men|people)\s+(?:who|that)\s+"
    r"(?:[\w-]+\s+){0,3}(?:saw|got|lost|gained|joined|reported|improved|dropped|achieved)\b"
    # "92% of members"
    r"|\b\d{1,3}\s?%\s+of\s+(?:members|clients|customers|women|men|people)"
    r"|\b(?:rated|testimonials?|satisfaction\s+rate|success\s+rate|reviews?)\b"
    r")",
    re.IGNORECASE,
)

# Numbers that read as evidence and need a source: percentages, rates,
# multiples, "years older/younger", weight lost. Prices, durations and
# session counts are product facts and are not flagged.
_STATISTIC = re.compile(
    r"(?:"
    r"\b\d{1,3}(?:\.\d+)?\s?%"
    r"|\b\d+(?:\.\d+)?\s*(?:x|times)\s+(?:more|faster|higher|likely)"
    r"|\b\d+\s*(?:to|-)\s*\d+\s+years\s+(?:older|younger)"
    r"|\bup\s+to\s+\d+"
    r"|\b\d+\s*(?:lbs|pounds|kg)\b"
    r"|\bper\s+decade\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CopyRuleViolation:
    field: str
    rule: str
    detail: str

    def as_flag(self) -> str:
        return f"{self.rule} in {self.field}: {self.detail}"


def customer_facing_text(brief: CreativeBrief) -> list[tuple[str, str]]:
    """(field, text) pairs a viewer could see, including overlay captions."""
    out: list[tuple[str, str]] = []
    for name in CUSTOMER_FACING_FIELDS:
        value = getattr(brief, name, None)
        if isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str) and item.strip():
                    out.append((f"{name}[{i}]", item))
        elif isinstance(value, str) and value.strip():
            out.append((name, value))
    for i, overlay in enumerate(brief.text_layout or []):
        text = getattr(overlay, "text", "")
        if text and text.strip():
            out.append((f"text_layout[{i}]", text))
    return out


def _excerpt(text: str, match: re.Match | None = None, width: int = 60) -> str:
    if match is None:
        return " ".join(text.split())[:width]
    start = max(0, match.start() - 20)
    return " ".join(text[start:match.end() + 20].split())


def check_brief_copy(
    brief: CreativeBrief,
    *,
    competitor_names: Iterable[str] = (),
    social_proof_available: bool = True,
    prohibited_terms: Iterable[str] = (),
) -> list[CopyRuleViolation]:
    """Return every rule the brief's customer-facing copy breaks."""
    violations: list[CopyRuleViolation] = []
    competitors = [c for c in competitor_names if c and c.strip()]
    competitor_re = (
        re.compile(r"\b(?:" + "|".join(re.escape(c) for c in competitors) + r")\b", re.IGNORECASE)
        if competitors else None
    )
    prohibited = [p for p in prohibited_terms if p and p.strip()]
    prohibited_re = (
        re.compile(r"(?:" + "|".join(re.escape(p) for p in prohibited) + r")", re.IGNORECASE)
        if prohibited else None
    )

    for field, text in customer_facing_text(brief):
        if any(d in text for d in _DASHES):
            n = sum(text.count(d) for d in _DASHES)
            violations.append(CopyRuleViolation(field, RULE_EM_DASH, f"{n} dash(es)"))
        if competitor_re is not None:
            m = competitor_re.search(text)
            if m:
                violations.append(CopyRuleViolation(field, RULE_COMPETITOR, f"'{m.group(0)}'"))
        m = _PERSONAL_ATTRIBUTE.search(text)
        if m:
            violations.append(CopyRuleViolation(field, RULE_PERSONAL_ATTRIBUTE, f"'{_excerpt(text, m)}'"))
        if not social_proof_available:
            m = _SOCIAL_PROOF.search(text)
            if m:
                violations.append(CopyRuleViolation(field, RULE_FABRICATED_PROOF, f"'{_excerpt(text, m)}'"))
        m = _STATISTIC.search(text)
        if m:
            violations.append(CopyRuleViolation(field, RULE_NEEDS_PROOF, f"'{_excerpt(text, m)}'"))
        if prohibited_re is not None:
            m = prohibited_re.search(text)
            if m:
                violations.append(CopyRuleViolation(field, RULE_PROHIBITED_TERM, f"'{m.group(0)}'"))

    return violations


def flag_strings(violations: Iterable[CopyRuleViolation]) -> list[str]:
    return [v.as_flag() for v in violations]
