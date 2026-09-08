"""Business-specific Exa query planning.

`strategy.exa_queries.default_queries_for_brand` is a category-neutral
fallback. This module asks the model to look at the client (brand context,
products, audience, competitors) and decide which searches will surface real
customer language for THAT business, then caches the decision as a reviewable
YAML at clients/<slug>/research/exa/query-plan.yaml. Operators can hand-edit
that file (set `provenance: manual`) and `adc research-web` uses it as-is.

Pure planning plus file IO. The only network call is the injected
`complete_fn`, so tests never touch the API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from models.skills import load_skill
from strategy.exa_queries import ExaQuery, slugify
from strategy.llm_yaml import strip_fences, try_repair_yaml

CLIENTS_DIR = Path("clients")
PLAN_FILENAME = "query-plan.yaml"
SKILL_NAME = "exa-query-planning"
FOOTPRINTS = ("established", "emerging", "pre_launch")
BRAND_PLACEHOLDER = "{brand}"
COMPETITOR_PLACEHOLDER = "{competitor}"
CONTEXT_CHAR_BUDGET = 9000
EXCLUDED_PRODUCT_FILES = {"example-product.yaml"}


class QueryPlanError(ValueError):
    """The plan is missing, malformed, or the model output was unusable."""


@dataclass
class PlannedAngle:
    label: str
    query: str
    category: str = "reviews"
    reddit: bool = False


@dataclass
class QueryPlan:
    business_type: str
    brand_footprint: str
    rationale: str
    brand_angles: list[PlannedAngle] = field(default_factory=list)
    competitor_angles: list[PlannedAngle] = field(default_factory=list)
    category_terms: list[str] = field(default_factory=list)
    provenance: str = "llm"
    generated_at: str = ""


def query_plan_path(client_slug: str) -> Path:
    return CLIENTS_DIR / client_slug / "research" / "exa" / PLAN_FILENAME


# ─── Validation ──────────────────────────────────────────────────────────────

def _dedupe(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(t.strip())
    return out


def _angles(raw, key: str, placeholder: str, required: bool) -> list[PlannedAngle]:
    if raw is None:
        raw = []
    if not isinstance(raw, list) or (required and not raw):
        raise QueryPlanError(f"{key} must be a non-empty list")
    angles: list[PlannedAngle] = []
    for item in raw:
        if not isinstance(item, dict):
            raise QueryPlanError(f"{key} entries must be mappings")
        label = slugify(str(item.get("label", "")))
        query = str(item.get("query", "")).strip()
        if not label or not query:
            raise QueryPlanError(f"{key} entries need a label and a query")
        if placeholder not in query:
            raise QueryPlanError(
                f"{key} query {query!r} must contain the placeholder {placeholder}"
            )
        angles.append(PlannedAngle(
            label=label,
            query=query,
            category=str(item.get("category", "reviews")).strip() or "reviews",
            reddit=bool(item.get("reddit", False)),
        ))
    return angles


def plan_from_dict(data) -> QueryPlan:
    if not isinstance(data, dict):
        raise QueryPlanError("query plan must be a YAML mapping")
    footprint = str(data.get("brand_footprint", "")).strip().lower()
    if footprint not in FOOTPRINTS:
        raise QueryPlanError(
            f"brand_footprint must be one of {FOOTPRINTS}, got {footprint!r}"
        )
    raw_terms = data.get("category_terms")
    if not isinstance(raw_terms, list):
        raise QueryPlanError("category_terms must be a non-empty list")
    terms = _dedupe([str(t) for t in raw_terms])
    if not terms:
        raise QueryPlanError("category_terms must be a non-empty list")
    return QueryPlan(
        business_type=str(data.get("business_type", "")).strip(),
        brand_footprint=footprint,
        rationale=str(data.get("rationale", "")).strip(),
        brand_angles=_angles(
            data.get("brand_angles"), "brand_angles", BRAND_PLACEHOLDER, required=False,
        ),
        competitor_angles=_angles(
            data.get("competitor_angles"), "competitor_angles",
            COMPETITOR_PLACEHOLDER, required=True,
        ),
        category_terms=terms,
        provenance=str(data.get("provenance", "llm")).strip() or "llm",
        generated_at=str(data.get("generated_at", "")).strip(),
    )


def plan_to_dict(plan: QueryPlan) -> dict:
    return asdict(plan)


# ─── Persistence ─────────────────────────────────────────────────────────────

def save_query_plan(client_slug: str, plan: QueryPlan) -> Path:
    path = query_plan_path(client_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(plan_to_dict(plan), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def load_query_plan(client_slug: str) -> QueryPlan | None:
    path = query_plan_path(client_slug)
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise QueryPlanError(f"{path} is not valid YAML: {err}") from err
    return plan_from_dict(data)


# ─── Context assembly ────────────────────────────────────────────────────────

def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _subset(data: dict, keys: tuple[str, ...]) -> dict:
    return {k: data[k] for k in keys if data.get(k) not in (None, "", [], {})}


def build_plan_context(client_slug: str) -> str:
    """Everything the planner needs to know about the business, as text."""
    client_dir = CLIENTS_DIR / client_slug
    brand_path = client_dir / "brand.yaml"
    if not brand_path.exists():
        raise FileNotFoundError(f"Brand file not found: {brand_path}")

    sections: list[str] = []
    brand = _subset(
        _read_yaml(brand_path),
        ("name", "tone", "audience", "mission", "tagline", "prohibited_terms"),
    )
    sections.append("## Brand\n" + yaml.safe_dump(brand, sort_keys=False, allow_unicode=True))

    context_path = client_dir / "brand-context.md"
    if context_path.exists():
        text = context_path.read_text(encoding="utf-8").strip()
        sections.append("## Brand context\n" + text[:CONTEXT_CHAR_BUDGET])

    products = []
    products_dir = client_dir / "products"
    if products_dir.exists():
        for path in sorted(products_dir.glob("*.yaml")):
            if path.name in EXCLUDED_PRODUCT_FILES:
                continue
            products.append(_subset(
                _read_yaml(path),
                ("name", "description", "category", "price", "benefits",
                 "unique_mechanism", "objections"),
            ))
    if products:
        sections.append(
            "## Products / offers\n"
            + yaml.safe_dump(products, sort_keys=False, allow_unicode=True)
        )

    comp_path = client_dir / "competitors.yaml"
    if comp_path.exists():
        comps = [
            _subset(c, ("name", "type", "priority", "notes"))
            for c in _read_yaml(comp_path).get("competitors", [])
            if isinstance(c, dict)
        ]
        if comps:
            sections.append(
                "## Competitors\n" + yaml.safe_dump(comps, sort_keys=False, allow_unicode=True)
            )

    return "\n\n".join(sections)


# ─── Generation ──────────────────────────────────────────────────────────────

def generate_query_plan(
    client_slug: str,
    complete_fn=None,
    max_tokens: int = 2048,
) -> QueryPlan:
    """One model pass: read the business, decide the searches, save the plan.

    Raises QueryPlanError when the model output cannot be parsed or fails
    validation even after one YAML repair pass. Nothing is written on failure.
    """
    if complete_fn is None:
        from strategy.llm import claude_complete
        complete_fn = claude_complete

    system = load_skill(SKILL_NAME)
    prompt = (
        "CLIENT CONTEXT\n\n"
        f"{build_plan_context(client_slug)}\n\n"
        "Plan the searches for this business now. Output the YAML only."
    )
    raw = complete_fn(prompt, system=system, max_tokens=max_tokens)
    text = strip_fences(raw)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        data = try_repair_yaml(text, err, complete_fn)
    if not isinstance(data, dict):
        raise QueryPlanError("model did not return a YAML mapping for the query plan")

    plan = plan_from_dict(data)
    plan.provenance = "llm"
    plan.generated_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    save_query_plan(client_slug, plan)
    return plan


# ─── Plan -> queries ─────────────────────────────────────────────────────────

def _brand_query(angle: PlannedAngle, brand_name: str, brand_slug: str) -> ExaQuery:
    query = angle.query.replace(BRAND_PLACEHOLDER, brand_name)
    if angle.reddit or angle.category == "reddit":
        return ExaQuery(
            label=f"reddit-{brand_slug}-{angle.label}",
            query=query,
            include_domains=["reddit.com"],
            category="reddit",
            keyword_query=angle.query.replace(BRAND_PLACEHOLDER, f'"{brand_name}"'),
        )
    return ExaQuery(
        label=f"web-{brand_slug}-{angle.label}",
        query=query,
        category=angle.category,
    )


def _competitor_query(angle: PlannedAngle, comp: str, comp_slug: str) -> ExaQuery:
    query = angle.query.replace(COMPETITOR_PLACEHOLDER, comp)
    if angle.reddit or angle.category == "reddit":
        return ExaQuery(
            label=f"reddit-{comp_slug}-{angle.label}",
            query=query,
            include_domains=["reddit.com"],
            category="reddit",
            keyword_query=angle.query.replace(COMPETITOR_PLACEHOLDER, f'"{comp}"'),
        )
    return ExaQuery(
        label=f"web-{comp_slug}-{angle.label}",
        query=query,
        category=angle.category,
    )


def queries_from_plan(
    plan: QueryPlan,
    brand_name: str,
    competitors: list[str] | None = None,
    extra_category_terms: list[str] | None = None,
) -> list[ExaQuery]:
    """Turn a plan into concrete ExaQuery objects.

    Label conventions match default_queries_for_brand so caches, status and
    downstream consumers are unaffected. Brand-name queries are skipped for a
    pre-launch brand: nobody has written about it yet, so they only burn
    calls. Operator `--category` terms are appended after the plan's own,
    de-duplicated case-insensitively.
    """
    competitors = competitors or []
    brand_slug = slugify(brand_name)
    queries: list[ExaQuery] = []

    if plan.brand_footprint != "pre_launch":
        queries.append(ExaQuery(
            label=f"reddit-{brand_slug}-honest",
            query=f"{brand_name} honest review experience",
            include_domains=["reddit.com"],
            category="reddit",
            keyword_query=f'"{brand_name}" review',
        ))
        queries.append(ExaQuery(
            label=f"reddit-{brand_slug}-worth-it",
            query=f"is {brand_name} worth it",
            include_domains=["reddit.com"],
            category="reddit",
            keyword_query=f'"{brand_name}" worth it',
        ))
        for angle in plan.brand_angles:
            queries.append(_brand_query(angle, brand_name, brand_slug))
        for comp in competitors:
            queries.append(ExaQuery(
                label=f"reddit-{brand_slug}-vs-{slugify(comp)}",
                query=f"{brand_name} vs {comp}",
                include_domains=["reddit.com"],
                category="comparison",
                keyword_query=f'"{brand_name}" vs "{comp}"',
            ))

    for comp in competitors:
        comp_slug = slugify(comp)
        for angle in plan.competitor_angles:
            queries.append(_competitor_query(angle, comp, comp_slug))

    for term in _dedupe(list(plan.category_terms) + list(extra_category_terms or [])):
        queries.append(ExaQuery(
            label=f"reddit-category-{slugify(term)}",
            query=f"{term} reddit discussion experiences",
            include_domains=["reddit.com"],
            category="category-discussion",
            keyword_query=term,
        ))

    return queries
