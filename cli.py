"""AdCreatives CLI — AI-powered ad creative generation for Meta and TikTok."""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

# Force UTF-8 on stdout/stderr before Rich initializes, so glyphs like ✓ render
# safely on Windows consoles that default to cp1252. No-op on POSIX.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _bootstrap_env_from_dotenv() -> None:
    """Populate os.environ from .env in the project root if keys are missing.

    Runs once at CLI startup so subprocess invocations (e.g. from the
    Streamlit dashboard) inherit API keys even when the parent process was
    launched without sourcing .env. Existing env vars are NOT overridden by
    default — shell-set keys win. No-op if .env is absent.

    Important on Windows: when a shell-set var is EMPTY (`KEY=`), the empty
    value would otherwise win over .env. We treat empty-string env vars as
    absent so .env values fill them in.
    """
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Override empty-string env vars too (common on Windows where the
        # shell sets the name but leaves the value empty).
        existing = os.environ.get(key, "")
        if key and not existing:
            os.environ[key] = value


_bootstrap_env_from_dotenv()
# Belt-and-suspenders: also let python-dotenv look for .env in any parent
# directory (handles worktrees where the .env is at the repo root).
load_dotenv(override=False)

console = Console()


@click.group()
def cli():
    """AdCreatives — Generate high-converting ad creatives with AI."""
    pass


# ─── Client Management ──────────────────────────────────────────────────────


@cli.command()
@click.option("--name", required=True, help="Client slug (lowercase, no spaces)")
def init_client(name: str):
    """Create a new client from the template."""
    src = Path("clients/_template")
    dest = Path("clients") / name

    if dest.exists():
        console.print(f"[red]Client '{name}' already exists at {dest}[/red]")
        raise SystemExit(1)

    shutil.copytree(src, dest)
    console.print(f"[green]Created client '{name}' at {dest}[/green]")
    console.print(f"  Edit {dest}/brand.yaml to configure brand identity")
    console.print(f"  Edit {dest}/products/example-product.yaml for your first product")
    console.print(f"  Add reviews to {dest}/voc/ for VOC mining")


@cli.command()
def list_clients():
    """List all configured clients."""
    from models.loader import list_clients as _list_clients, list_products

    clients = _list_clients()
    if not clients:
        console.print("[yellow]No clients found. Run: adc init-client --name your-client[/yellow]")
        return

    table = Table(title="Clients")
    table.add_column("Client", style="cyan")
    table.add_column("Products", style="green")

    for client in clients:
        products = list_products(client)
        table.add_row(client, ", ".join(products) or "[dim]none[/dim]")

    console.print(table)


# ─── Personas (Stage 2) ─────────────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--max-personas", default=5, type=int,
              help="Maximum number of personas to generate (1-6). Default 5 — "
              "use --max-personas 3 if you want a tighter set.")
def personas(client: str, max_personas: int):
    """Expand single avatar to multiple structured personas.

    Reads brand-context.md (which already identifies audience tiers) and
    generates one full CustomerAvatar YAML per persona under
    clients/<slug>/avatars/<persona-id>.yaml plus an _index.yaml roster.

    Each persona is genuinely distinct — different pains, triggers, awareness
    levels — so downstream stages (strategy matrix, brief) can target them.
    """
    from models.loader import load_brand
    from strategy.personas import build_personas, save_personas

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    context_path = client_dir / "brand-context.md"
    if not context_path.exists():
        console.print(
            f"[red]No brand-context.md at {context_path}. Run `adc research` first.[/red]"
        )
        raise SystemExit(1)

    brand = load_brand(client)
    brand_context_md = context_path.read_text(encoding="utf-8")

    console.print(
        f"\n[bold cyan]Expanding personas for {brand.name}[/bold cyan] "
        f"(up to {max_personas})"
    )

    with console.status("Identifying tiers + building personas with Claude Sonnet 4.6..."):
        result = build_personas(
            brand=brand,
            brand_context_md=brand_context_md,
            max_personas=max_personas,
            client_slug=client,
        )

    if not result.personas:
        console.print("[yellow]No personas generated. Check brand-context.md content.[/yellow]")
        raise SystemExit(1)

    index_path, written = save_personas(client, result)
    console.print(f"\n[green]Wrote {len(written)} persona file(s):[/green]")
    for p in written:
        console.print(f"  - {p}")
    console.print(f"[green]Wrote roster: {index_path}[/green]")
    console.print()

    table = Table(title=f"Personas for {brand.name}")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Role", style="yellow")
    table.add_column("Awareness", style="dim")
    table.add_column("Confidence", style="dim")
    for p in result.index.get("personas", []):
        table.add_row(
            p.get("id", ""),
            p.get("name", ""),
            p.get("role", ""),
            p.get("awareness_level", ""),
            p.get("confidence", ""),
        )
    console.print(table)


# ─── Persona add / delete (single-persona management) ───────────────────────


@cli.command(name="add-persona")
@click.option("--client", required=True, help="Client slug")
def add_persona_cmd(client: str):
    """Generate ONE new persona that fills a gap in the existing set.

    Loads existing avatars from clients/<slug>/avatars/, summarizes them for
    the LLM, then asks for a single new persona that differs on pains,
    triggers, language, or awareness level. Saves as a new avatar file and
    appends to _index.yaml. Enforces a hard cap of 6 personas per client.
    """
    from models.loader import load_brand, load_all_avatars
    from strategy.personas import (
        MAX_PERSONAS,
        add_persona,
        build_one_persona,
    )

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    existing = load_all_avatars(client)
    if len(existing) >= MAX_PERSONAS:
        names = ", ".join(a.name or "?" for a in existing)
        console.print(
            f"[red]At persona cap ({len(existing)}/{MAX_PERSONAS}). "
            f"Delete one first with `adc delete-persona --client {client} --avatar <slug>`.[/red]"
        )
        console.print(f"[dim]Current personas: {names}[/dim]")
        raise SystemExit(1)

    context_path = client_dir / "brand-context.md"
    if not context_path.exists():
        console.print(
            f"[red]No brand-context.md at {context_path}. Run `adc research` first.[/red]"
        )
        raise SystemExit(1)

    brand = load_brand(client)
    brand_context_md = context_path.read_text(encoding="utf-8")

    console.print(
        f"\n[bold cyan]Adding one persona for {brand.name}[/bold cyan] "
        f"({len(existing)} → {len(existing) + 1} of {MAX_PERSONAS})"
    )
    with console.status("Generating a distinct new persona with Claude Sonnet 4.6..."):
        try:
            persona = build_one_persona(
                brand=brand,
                brand_context_md=brand_context_md,
                existing_avatars=existing,
                client_slug=client,
            )
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    avatar_path, index_path = add_persona(client, persona)
    console.print(f"\n[green]Created persona:[/green] {persona.get('name', '?')}")
    console.print(f"  - File:  {avatar_path}")
    console.print(f"  - Slug:  {persona['id']}")
    console.print(f"  - Index: {index_path}")
    console.print(
        f"\n[green]Next:[/green] adc profile-psychology --client {client} --avatar {persona['id']}"
    )

    from strategy.cost_tracker import log_cost
    log_cost(client, "adc add-persona", note=f"added {persona['id']}")


@cli.command(name="delete-persona")
@click.option("--client", required=True, help="Client slug")
@click.option("--avatar", "avatar_slug", required=True,
              help="Avatar slug to delete (e.g. 'tertiary' or 'switcher-stacey'). "
              "Matches the filename stem in clients/<slug>/avatars/.")
@click.option("--yes", is_flag=True, default=False,
              help="Skip the confirmation prompt.")
def delete_persona_cmd(client: str, avatar_slug: str, yes: bool):
    """Remove a persona and prune it from the _index.yaml roster.

    Existing briefs that reference this persona by name are NOT touched —
    they keep their persona text baked in. Only the avatar file and the
    index entry are removed.
    """
    from strategy.personas import delete_persona

    avatar_path = Path("clients") / client / "avatars" / f"{avatar_slug}.yaml"
    if not avatar_path.exists():
        console.print(f"[red]Avatar not found: {avatar_path}[/red]")
        raise SystemExit(1)

    if not yes:
        if not click.confirm(
            f"Delete persona '{avatar_slug}' from client '{client}'? "
            f"This removes {avatar_path} permanently."
        ):
            console.print("[yellow]Aborted.[/yellow]")
            return

    ok, removed = delete_persona(client, avatar_slug)
    if not ok:
        console.print(f"[red]Failed to delete: {avatar_path}[/red]")
        raise SystemExit(1)
    console.print(f"[green]Deleted:[/green] {removed}")


# ─── Persona portraits (model library for identity-preserving ad gen) ────────


@cli.command()
@click.option("--client", required=True, help="Client slug (will be created if missing)")
@click.option("--url", required=True, help="Brand homepage URL")
@click.option("--max-products", default=3, type=int)
@click.option("--max-personas", default=3, type=int)
@click.option("--skip", multiple=True,
              type=click.Choice(["research", "personas", "product-deep-dive", "offers", "strategy-matrix"]),
              help="Stage(s) to skip (can be repeated)")
@click.pass_context
def onboard(ctx, client: str, url: str, max_products: int, max_personas: int, skip: tuple):
    """Run the full onboarding pipeline (stages 1-5) end-to-end.

    Sequence: research → product-deep-dive → personas → offers → strategy-matrix.
    Each stage builds on the previous. Use --skip to omit any stage that's
    already done or not needed.

    All stages run with --auto where applicable. Use the individual commands
    if you want interactive review.
    """
    skipped = set(skip)

    def banner(num: int, name: str):
        console.print()
        console.print(f"[bold magenta]{'═' * 60}[/bold magenta]")
        console.print(f"[bold magenta]STAGE {num} — {name.upper()}[/bold magenta]")
        console.print(f"[bold magenta]{'═' * 60}[/bold magenta]")

    if "research" in skipped:
        console.print("[yellow]Skipping research[/yellow]")
    else:
        banner(1, "Research")
        ctx.invoke(research, client=client, url=url, max_products=max_products, auto=True)

    if "product-deep-dive" in skipped:
        console.print("[yellow]Skipping product-deep-dive[/yellow]")
    else:
        banner(4, "Product Deep-Dive")
        ctx.invoke(product_deep_dive, client=client, product=None)

    if "personas" in skipped:
        console.print("[yellow]Skipping personas[/yellow]")
    else:
        banner(2, "Personas")
        ctx.invoke(personas, client=client, max_personas=max_personas)

    if "offers" in skipped:
        console.print("[yellow]Skipping offers[/yellow]")
    else:
        banner(3, "Offers")
        ctx.invoke(offers, client=client, url=url)

    if "strategy-matrix" in skipped:
        console.print("[yellow]Skipping strategy-matrix[/yellow]")
    else:
        banner(5, "Strategy Matrix")
        ctx.invoke(strategy_matrix, client=client, max_products=max_products)

    console.print()
    console.print(f"[bold green]✓ Onboarding complete for '{client}'[/bold green]")
    console.print()
    console.print("[bold]Files now under clients/{}/:[/bold]".format(client))
    console.print("  - brand.yaml, brand-context.md")
    console.print("  - avatar.yaml + avatars/<id>.yaml × N")
    console.print("  - products/<id>.yaml × N (enriched)")
    console.print("  - offers.yaml")
    console.print("  - strategy-matrix.md, strategy-matrix.yaml")
    console.print()
    console.print("[bold]Next:[/bold]")
    console.print(
        f"  1. adc mine-voc --client {client} --category <category>  "
        "[dim](optional but recommended)[/dim]"
    )
    console.print(
        f"  2. adc profile-psychology --client {client}  "
        "[dim](diagnose buyer heuristics + pairings)[/dim]"
    )
    console.print(
        f"  3. adc brief --client {client} --product <id> --angles 6"
    )


# ─── Product Deep-Dive (Stage 4) ────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--product", default=None,
              help="Specific product slug to enrich (omit to enrich all)")
def product_deep_dive(client: str, product: str | None):
    """Fetch product detail pages and enrich product YAMLs with benefits + reviews.

    For each product (or one specific product if --product given), fetches
    its detail page and runs an LLM extraction using motion/review-audit +
    coreyhaines/customer-research as system context. Pulls functional/
    emotional/social benefits, unique mechanism, real price, objections,
    review quotes, and customer language verbatim quotes.

    Updates clients/<slug>/products/*.yaml in place — preserves existing
    fields, fills in empty ones, appends new lists with dedup.
    """
    from models.loader import (
        list_products as _list_products,
        load_brand,
        load_product,
    )
    from strategy.product_dive import deep_dive_products

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    brand = load_brand(client)

    if product:
        try:
            products = [load_product(client, product)]
        except Exception as e:
            console.print(f"[red]Failed to load product '{product}': {e}[/red]")
            raise SystemExit(1)
    else:
        products = []
        for slug in _list_products(client):
            if slug.startswith("example"):
                continue
            try:
                products.append(load_product(client, slug))
            except Exception:
                continue

    if not products:
        console.print(f"[yellow]No products to deep-dive for '{client}'.[/yellow]")
        raise SystemExit(0)

    products_with_url = [p for p in products if p.url]
    products_without_url = [p for p in products if not p.url]

    console.print(
        f"\n[bold cyan]Deep-diving {len(products_with_url)} product page(s) for {brand.name}[/bold cyan]"
    )
    if products_without_url:
        console.print(
            f"[yellow]Skipping {len(products_without_url)} product(s) without URL: "
            f"{', '.join(p.name for p in products_without_url)}[/yellow]"
        )

    with console.status("Fetching product pages + extracting with Sonnet 4.6..."):
        summary = deep_dive_products(client, brand, products_with_url)

    table = Table(title="Product enrichment results")
    table.add_column("Product", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Price", style="yellow")
    table.add_column("Benefits", style="dim")
    table.add_column("Quotes", style="dim")
    table.add_column("Reviews API", style="magenta")
    table.add_column("Confidence", style="dim")
    for name, info in summary.items():
        table.add_row(
            name[:40],
            info.get("status", "?"),
            str(info.get("price", ""))[:30],
            str(info.get("benefit_count", "")),
            str(info.get("social_proof_count", "")),
            f"{info.get('review_vendor', 'none')} ({info.get('reviews_fetched', 0)})",
            str(info.get("confidence", "")),
        )
    console.print(table)


# ─── Offers (Stage 3) ───────────────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--url", default=None,
              help="Brand homepage URL — defaults to brand context if available")
def offers(client: str, url: str | None):
    """Extract existing offers + generate suggested offers for a client.

    Crawls FAQ, shipping/returns policies, subscription pages on the brand's
    site for offers already running. Then runs offer engineering principles
    (value equation, offer stack, premium positioning) over the brand context
    to suggest new offers tailored to the brand.

    Output: clients/<slug>/offers.yaml
    """
    from models.loader import (
        list_products as _list_products,
        load_avatar,
        load_brand,
        load_product,
    )
    from strategy.offers import build_offers, fetch_offer_pages, save_offers
    from strategy.researcher import fetch_pages

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    brand = load_brand(client)
    avatar = load_avatar(client)
    avatars = [avatar] if avatar else []

    # Pull additional personas if Stage 2 has run
    avatars_dir = client_dir / "avatars"
    if avatars_dir.exists():
        from models.avatar import CustomerAvatar
        for f in sorted(avatars_dir.glob("*.yaml")):
            if f.name.startswith("_"):
                continue
            try:
                with open(f, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                avatars.append(CustomerAvatar(**data))
            except Exception:
                continue

    product_slugs = [s for s in _list_products(client) if not s.startswith("example")]
    products = []
    for slug in product_slugs[:5]:
        try:
            products.append(load_product(client, slug))
        except Exception:
            continue

    if not url:
        url = _infer_url_from_products(products)
    if not url:
        console.print(
            "[red]No URL provided and couldn't infer one from products. "
            "Pass --url https://yourbrand.com[/red]"
        )
        raise SystemExit(1)

    brand_context_md = ""
    context_path = client_dir / "brand-context.md"
    if context_path.exists():
        brand_context_md = context_path.read_text(encoding="utf-8")

    homepage_html = ""
    with console.status(f"Fetching homepage + offer pages from {url}..."):
        homepage_pages = fetch_pages(url, paths=["/"])
        if homepage_pages:
            homepage_html = next(iter(homepage_pages.values()))
        offer_pages = fetch_offer_pages(url)

    console.print(
        f"[green]Fetched {len(offer_pages)} offer-bearing pages[/green]"
        + (" + homepage" if homepage_html else "")
    )

    console.print(
        f"\n[bold cyan]Extracting + generating offers for {brand.name}[/bold cyan]"
    )

    with console.status("Extracting existing + generating suggested offers (Sonnet 4.6)..."):
        result = build_offers(
            brand=brand,
            avatars=avatars,
            products=products,
            offer_pages=offer_pages,
            homepage_html=homepage_html,
            brand_context_md=brand_context_md,
        )

    out_path = save_offers(client, result)
    console.print(f"\n[green]Wrote {out_path}[/green]\n")

    if result.existing_offers:
        table = Table(title=f"Existing offers ({len(result.existing_offers)})")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Where", style="dim")
        for o in result.existing_offers:
            table.add_row(
                str(o.get("name", ""))[:50],
                str(o.get("type", "")),
                str(o.get("where_found", ""))[:40],
            )
        console.print(table)

    if result.suggested_offers:
        table = Table(title=f"Suggested offers ({len(result.suggested_offers)})")
        table.add_column("Name", style="cyan")
        table.add_column("Type", style="yellow")
        table.add_column("Persona", style="green")
        table.add_column("Lift", style="dim")
        for o in result.suggested_offers:
            table.add_row(
                str(o.get("name", ""))[:50],
                str(o.get("type", "")),
                str(o.get("target_persona", "")),
                str(o.get("estimated_lift", "")),
            )
        console.print(table)

    notes = result.notes or {}
    if notes.get("highest_priority_test"):
        console.print(
            f"\n[bold]Highest priority test:[/bold] {notes['highest_priority_test']}"
        )


def _infer_url_from_products(products: list) -> str | None:
    """Extract a brand URL from product page URLs if available."""
    for p in products:
        if p.url and p.url.startswith("http"):
            from urllib.parse import urlparse
            parsed = urlparse(p.url)
            return f"{parsed.scheme}://{parsed.netloc}"
    return None


# ─── Strategy Matrix (Stage 5) ──────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--max-products", default=3, type=int, help="How many products to include in context")
def strategy_matrix(client: str, max_products: int):
    """Generate a Schwartz × persona strategy matrix for a client.

    Reads brand.yaml, avatar.yaml, brand-context.md, and product YAMLs.
    Produces strategy-matrix.md (human-readable) and strategy-matrix.yaml
    (structured) under clients/<slug>/.

    Each matrix cell maps one persona × one awareness stage to specific
    messaging guidance: angle, hook style, example hook, framework,
    creative mechanic, proof to surface, CTA, funnel placement.
    """
    from models.loader import (
        list_products as _list_products,
        load_avatar,
        load_brand,
        load_product,
    )
    from strategy.matrix import build_strategy_matrix, save_matrix

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    brand = load_brand(client)
    avatar = load_avatar(client)
    if not avatar:
        console.print(f"[red]No avatar found for '{client}'. Run `adc research` first.[/red]")
        raise SystemExit(1)

    product_slugs = [s for s in _list_products(client) if not s.startswith("example")]
    products = []
    for slug in product_slugs[:max_products]:
        try:
            products.append(load_product(client, slug))
        except Exception as e:
            console.print(f"[yellow]Skipping product {slug}: {e}[/yellow]")

    brand_context_md = ""
    context_path = client_dir / "brand-context.md"
    if context_path.exists():
        brand_context_md = context_path.read_text(encoding="utf-8")

    # Load the full persona roster from avatars/ when Stage 2 has run.
    # Falls back to the single legacy avatar.yaml if the roster doesn't exist.
    avatars = [avatar]
    avatars_dir = client_dir / "avatars"
    if avatars_dir.exists():
        import yaml as _yaml
        from models.avatar import CustomerAvatar
        roster: list = []
        for f in sorted(avatars_dir.glob("*.yaml")):
            if f.name.startswith("_") or f.name.endswith(".bak"):
                continue
            try:
                with open(f, encoding="utf-8") as fh:
                    data = _yaml.safe_load(fh)
                roster.append(CustomerAvatar(**data))
            except Exception:
                continue
        if roster:
            avatars = roster

    console.print(
        f"\n[bold cyan]Building strategy matrix[/bold cyan] — "
        f"{len(avatars)} persona × 5 awareness stages = {len(avatars) * 5} cells"
    )
    console.print(f"  brand: {brand.name}")
    console.print(f"  products in scope: {', '.join(p.name for p in products) or '(none)'}")
    console.print()

    with console.status("Compiling matrix with Claude Sonnet 4.6 (motion/creative-strategy-engine + product-marketing-context)..."):
        result = build_strategy_matrix(
            brand=brand,
            avatars=avatars,
            products=products,
            brand_context_md=brand_context_md,
            client_slug=client,
        )

    md_path, yaml_path = save_matrix(client, result)
    cell_count = sum(len(p.get("cells", [])) for p in result.data.get("matrix", []))

    console.print(f"[green]Wrote {md_path}[/green]")
    console.print(f"[green]Wrote {yaml_path}[/green]")
    console.print(f"[dim]{cell_count} matrix cells generated[/dim]")
    console.print()

    obs = result.data.get("cross_stage_observations") or {}
    if obs.get("highest_leverage_stages"):
        console.print(
            f"[bold]Highest leverage stages:[/bold] "
            f"{', '.join(obs['highest_leverage_stages'])}"
        )
    if obs.get("ad_distribution_recommendation"):
        console.print(f"[bold]Recommended distribution:[/bold] {obs['ad_distribution_recommendation']}")


# ─── Voice (Stage 11 — sender-side voice layer) ─────────────────────────────


@cli.command(name="voice")
@click.option("--client", required=True, help="Client slug")
def voice(client: str):
    """Find the brand's ownable voice — the unspoken-truths hook bank.

    Reads the personas' verbatim language, the VoC corpus, and the competitive
    context, then defines how the brand should SOUND in the gap between how
    customers actually talk and how the category sounds. Writes voice.md
    (human-readable) + voice.yaml (structured) under clients/<slug>/.

    The heavy-lifting output is a persona-tagged bank of unspoken truths —
    specific lived-experience lines that are already hooks. Downstream brief +
    copy generation pulls from this bank.

    Run AFTER personas (and ideally `adc mine-voc` + `adc analyze-gaps`) for the
    richest grounding. Sender-side twin of `adc profile-psychology`.
    """
    from rich.markup import escape

    from models.loader import load_all_avatars, load_avatar, load_brand
    from strategy.brand_voice import build_voice, save_voice

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    brand = load_brand(client)

    avatars = load_all_avatars(client)
    if not avatars:
        legacy = load_avatar(client)
        if legacy:
            avatars = [legacy]
    if not avatars:
        console.print(
            f"[red]No personas for '{client}'. Run `adc personas --client {client}` "
            f"(or `adc research`) first — the voice is mined from verbatim persona "
            f"language.[/red]"
        )
        raise SystemExit(1)

    brand_context_md = ""
    context_path = client_dir / "brand-context.md"
    if context_path.exists():
        brand_context_md = context_path.read_text(encoding="utf-8")

    console.print(
        f"\n[bold cyan]Finding the ownable voice for {brand.name}[/bold cyan] "
        f"— {len(avatars)} persona(s)"
    )
    console.print(
        "[dim]Mining the gap between how customers talk and how the category sounds...[/dim]"
    )

    with console.status("Building brand voice with Claude Sonnet 4.6 (brand-voice skill)..."):
        try:
            result = build_voice(
                brand=brand,
                avatars=avatars,
                brand_context_md=brand_context_md,
                client_slug=client,
            )
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    md_path, yaml_path = save_voice(client, result)
    voice_obj = result.voice

    console.print(f"\n[green]Wrote {md_path}[/green]")
    console.print(f"[green]Wrote {yaml_path}[/green]\n")

    if voice_obj.territory:
        console.print(f"[bold]Territory:[/bold] {escape(voice_obj.territory)}\n")

    total_truths = sum(len(p.unspoken_truths) for p in voice_obj.personas)
    table = Table(title=f"Unspoken-truths bank ({total_truths} lines)")
    table.add_column("Persona", style="cyan")
    table.add_column("Truths", style="green", justify="right")
    table.add_column("Sample line", style="dim")
    for pv in voice_obj.personas:
        sample = pv.unspoken_truths[0] if pv.unspoken_truths else ""
        sample = (sample[:70] + "…") if len(sample) > 70 else sample
        table.add_row(
            escape(pv.persona_name or pv.persona_id or "?"),
            str(len(pv.unspoken_truths)),
            escape(sample),
        )
    console.print(table)

    console.print(
        f"\n[bold]Checkpoint:[/bold] read the bank in {md_path} and spot-check it — "
        f"is this the brand, or too far? At least one line should be something the "
        f"brand would never currently say (that's the point)."
    )
    console.print(
        f"\n[bold]Next:[/bold] adc brief --client {client} --product <id> --angles 6 "
        f"[dim](briefs + copy pull hooks from the voice bank)[/dim]"
    )

    from strategy.cost_tracker import log_cost

    log_cost(client, "adc voice", note=f"{len(avatars)} persona(s), {total_truths} truths")


# ─── Brand Research (auto + interactive) ────────────────────────────────────


def _flatten(field):
    """Strip {value, confidence, source} envelopes recursively to plain values."""
    if isinstance(field, dict) and "value" in field and "confidence" in field:
        return field["value"]
    if isinstance(field, dict):
        return {k: _flatten(v) for k, v in field.items()}
    if isinstance(field, list):
        return [_flatten(item) for item in field]
    return field


def _slugify(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "untitled"


@cli.command()
@click.option("--client", required=True, help="Client slug (will be created if missing)")
@click.option("--url", required=True, help="Brand homepage URL")
@click.option("--max-products", default=3, type=int, help="Max products to focus on")
@click.option("--auto/--review", default=False,
              help="--auto skips PHASE 4 confirmations and accepts all extractions as-is")
def research(client: str, url: str, max_products: int, auto: bool):
    """Brand research using Motion's interview-first methodology.

    Phase 1: 6 batched seed questions about products, audience, competitors,
    constraints, and existing creative.
    Phase 2: Web research — fetches homepage and standard sub-pages.
    Phase 3: Compiles a comprehensive brand-context.md doc using the
    motion/brand-intake skill + structured data with confidence tagging.
    Phase 4: Interactive review — confirm extractions, fill gaps, write YAMLs.
    """
    import shutil
    import yaml as _yaml

    from strategy.researcher import (
        INTAKE_QUESTIONS,
        confidence_buckets,
        discover_product_urls_smart,
        discover_visual_identity_images,
        extract_visual_identity,
        fetch_homepage_html,
        fetch_pages,
        fetch_product_pages,
        fetch_shopify_bestsellers,
        is_shopify_site,
        parse_shopify_product_cards,
        run_brand_intake,
    )

    client_dir = Path("clients") / client

    if not client_dir.exists():
        if not click.confirm(
            f"Client '{client}' doesn't exist. Create from template?", default=True
        ):
            raise SystemExit(0)
        shutil.copytree("clients/_template", client_dir)
        console.print(f"[green]Created clients/{client}/[/green]")
    elif (client_dir / "brand.yaml").exists():
        console.print(f"[yellow]Warning: clients/{client}/brand.yaml already exists.[/yellow]")
        if not click.confirm("Overwrite existing files at the end?", default=False):
            raise SystemExit(0)

    # ─── PHASE 1: INTAKE INTERVIEW ────────────────────────────────────────
    console.print("\n[bold cyan]PHASE 1 — INTAKE INTERVIEW[/bold cyan]")
    console.print("[dim]Answer all questions before research begins. Type 'skip' to leave blank.[/dim]\n")
    brand_name = click.prompt("  Brand name", default="")
    console.print()
    for q in INTAKE_QUESTIONS:
        console.print(f"  [yellow]Q[/yellow] {q['prompt']}")
    console.print()

    seed_answers: dict[str, str] = {}
    for q in INTAKE_QUESTIONS:
        ans = click.prompt(f"  [{q['key']}]", default=q["default"])
        seed_answers[q["key"]] = "" if ans.lower() == "skip" else ans

    # ─── PHASE 2: WEB RESEARCH ────────────────────────────────────────────
    console.print("\n[bold cyan]PHASE 2 — WEB RESEARCH[/bold cyan]")
    with console.status(f"Fetching pages from {url}..."):
        pages = fetch_pages(url)

    if not pages:
        console.print(f"[red]No pages fetched from {url}. Check the URL.[/red]")
        raise SystemExit(1)
    console.print(f"[green]Fetched {len(pages)} pages:[/green]")
    for page_url in pages:
        console.print(f"  - {page_url}")

    # Fetch the homepage as RAW HTML (Firecrawl rendered or httpx) so parsers
    # downstream get <head>/<meta>/<script> — Markdown strips those.
    homepage_html = fetch_homepage_html(url)
    if not homepage_html:
        # Last-resort: best-effort guess from whatever fetch_pages collected
        homepage_html = next(iter(pages.values()), "")

    bestsellers = []
    if is_shopify_site(homepage_html):
        console.print("\n[cyan]Detected Shopify store — fetching best-sellers (3 pages)...[/cyan]")
        with console.status("Pulling /collections/all?sort_by=best-selling..."):
            best_pages = fetch_shopify_bestsellers(url, page_count=3)
            for page_url, page_html in best_pages:
                cards = parse_shopify_product_cards(page_html, url)
                # Re-rank by overall position across pages
                for card in cards:
                    card.rank = len(bestsellers) + 1
                    bestsellers.append(card)
        console.print(f"[green]Parsed {len(bestsellers)} best-selling products from {len(best_pages)} pages.[/green]")
        if bestsellers:
            console.print("  [dim]Top 5:[/dim]")
            for c in bestsellers[:5]:
                console.print(f"    [dim]{c.rank}. {c.name}[/dim]")

    # ── Product page (PDP) discovery + fetch ──────────────────────────────
    # Pricing, ingredients, and detailed product copy only live on PDPs —
    # the candidate-path list never visits them. Use Firecrawl /map to
    # discover PDPs, prefer those that appeared as bestsellers, and merge
    # the top 3 into the pages dict before brand-intake compilation.
    pdp_urls_from_map = discover_product_urls_smart(url, limit=12)
    bestseller_pdp_urls: list[str] = []
    for c in bestsellers:
        if c.url and "/products/" in c.url and "menu_drawer" not in c.url:
            if c.url not in bestseller_pdp_urls:
                bestseller_pdp_urls.append(c.url)

    # Ranked PDP list: bestsellers first (preserve their order), then any
    # remaining /map-discovered PDPs not in that set.
    pdp_urls: list[str] = []
    seen: set[str] = set()
    for u in bestseller_pdp_urls + pdp_urls_from_map:
        if u not in seen:
            seen.add(u)
            pdp_urls.append(u)
    pdp_urls = pdp_urls[:3]

    if pdp_urls:
        console.print(f"\n[cyan]Fetching {len(pdp_urls)} product page(s) for pricing + ingredients:[/cyan]")
        for u in pdp_urls:
            console.print(f"  - {u}")
        pdp_pages = fetch_product_pages(pdp_urls)
        if pdp_pages:
            console.print(f"[green]Captured {len(pdp_pages)} PDP(s).[/green]")
            pages.update(pdp_pages)
        else:
            console.print("[yellow]PDP fetch returned nothing.[/yellow]")

    # Visual identity capture (multi-image, Gemini 2.5 Pro via OpenRouter,
    # falls back to Claude vision if OPENROUTER_API_KEY not set).
    # Brand colors are NOT extracted — clients fill those in manually.
    visual_identity = None
    vi_images = discover_visual_identity_images(homepage_html, url, bestsellers=bestsellers)
    if vi_images:
        console.print(f"\n[cyan]Visual identity capture — analyzing {len(vi_images)} image(s):[/cyan]")
        for img in vi_images:
            console.print(f"  - {img[:100]}")
        with console.status("Running multi-image vision (Gemini 2.5 Pro / Claude fallback)..."):
            visual_identity = extract_visual_identity(vi_images)
        if visual_identity:
            console.print(f"[green]Visual identity captured.[/green] Aesthetic: "
                          f"{visual_identity.get('aesthetic', '')[:100]}")
        else:
            console.print("[yellow]Visual identity extraction returned nothing — brand-context will be text-only.[/yellow]")
    else:
        console.print("[yellow]No images found for visual identity analysis.[/yellow]")

    # ─── PHASE 3: BUILD BRAND CONTEXT ─────────────────────────────────────
    console.print("\n[bold cyan]PHASE 3 — BUILDING BRAND CONTEXT[/bold cyan]")
    with console.status("Compiling brand-context.md + structured data with Claude Sonnet 4.6..."):
        result = run_brand_intake(
            brand_name=brand_name,
            brand_url=url,
            seed_answers=seed_answers,
            pages=pages,
            bestsellers=bestsellers,
            visual_identity=visual_identity,
        )
    data = result.data
    console.print("[green]Compiled.[/green]\n")

    context_path = client_dir / "brand-context.md"
    context_path.write_text(result.brand_context_md, encoding="utf-8")
    console.print(f"[green]Wrote {context_path}[/green]")
    console.print(f"[dim]Open it: cat {context_path}[/dim]\n")

    # ─── PHASE 4: REVIEW & CONFIRM ────────────────────────────────────────
    console.print("[bold cyan]PHASE 4 — REVIEW EXTRACTIONS[/bold cyan]\n")
    if auto:
        console.print("[dim](--auto mode: skipping interactive review, accepting all extractions)[/dim]\n")

    # Visual identity gets a dedicated display (it's the most actionable
    # output for downstream creative generation).
    brand = data.get("brand", {})
    vi = brand.get("visual_identity") or {}
    if vi:
        console.print("[bold magenta]VISUAL IDENTITY (from multi-image vision):[/bold magenta]")
        for key in ("aesthetic", "design_language", "photography_style", "typography_feel",
                    "mascot_or_character", "color_mood", "mood"):
            val = vi.get(key)
            if val:
                console.print(f"  [cyan]{key}[/cyan]: {val}")
        for key in ("visual_references", "notable_visual_signatures"):
            items = vi.get(key) or []
            if items:
                console.print(f"  [cyan]{key}[/cyan]:")
                for item in items[:5]:
                    console.print(f"    - {item}")
        console.print()
        console.print("  [dim]Brand colors are NOT auto-extracted — fill them in manually in brand.yaml.[/dim]\n")

    buckets = confidence_buckets(data)

    if buckets["high"]:
        console.print(f"[bold green]HIGH CONFIDENCE — {len(buckets['high'])} items auto-accepted:[/bold green]")
        for path, field in buckets["high"]:
            val = field.get("value")
            display = str(val)[:80] + ("..." if len(str(val)) > 80 else "")
            console.print(f"  [green]✓[/green] {path}: {display!r}")
            console.print(f"      [dim]from {field.get('source', '')}[/dim]")
        console.print()

    if buckets["medium"]:
        console.print(f"[bold yellow]MEDIUM CONFIDENCE — {len(buckets['medium'])} items:[/bold yellow]")
        for path, field in buckets["medium"]:
            console.print(f"\n  {path}: {field.get('value')!r}")
            console.print(f"  [dim]source: {field.get('source', '')}[/dim]")
            if not auto and not click.confirm("  Accept?", default=True):
                new_val = click.prompt("  Correct value (or empty to skip)", default="")
                if new_val:
                    field["value"] = new_val
        console.print()

    if buckets["low"] or buckets["unknown"]:
        items = buckets["low"] + buckets["unknown"]
        console.print(f"[bold red]LOW / UNKNOWN — {len(items)} items:[/bold red]")
        for path, field in items:
            console.print(f"\n  {path}")
            current = field.get("value")
            if current:
                console.print(f"  [dim]My guess: {current!r} ({field.get('source', 'inference')})[/dim]")
            if not auto:
                answer = click.prompt("  Value (or 'skip')", default=str(current) if current else "skip")
                if answer.lower() != "skip":
                    field["value"] = answer
        console.print()

    questions = data.get("questions_for_user", []) or []
    if questions and not auto:
        console.print(f"[bold cyan]LLM ASKS — {len(questions)} clarifying questions:[/bold cyan]")
        extra_answers = {}
        for q in questions:
            console.print(f"\n  [cyan]{q.get('field', '?')}[/cyan]")
            console.print(f"  Q: {q.get('question', '')}")
            console.print(f"  [dim]Why: {q.get('why_asking', '')}[/dim]")
            answer = click.prompt("  A (or 'skip')", default="skip")
            if answer.lower() != "skip":
                extra_answers[q.get("field", "")] = answer
        for field_path, value in extra_answers.items():
            parts = field_path.split(".")
            target = data
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            if isinstance(target.get(parts[-1]), dict) and "value" in target[parts[-1]]:
                target[parts[-1]]["value"] = value
            else:
                target[parts[-1]] = {"value": value, "confidence": "high", "source": "user"}
    elif questions and auto:
        console.print(f"[dim]LLM had {len(questions)} clarifying questions — skipped in --auto mode.[/dim]")
        for q in questions:
            console.print(f"  [dim]- {q.get('field')}: {q.get('question')}[/dim]")

    products = data.get("products", []) or []
    chosen_products = []
    if products:
        console.print(f"\n[bold]PRODUCTS — {len(products)} found:[/bold]")
        for i, p in enumerate(products, 1):
            name = _flatten(p.get("name", "?"))
            price = _flatten(p.get("price", "?"))
            hero = " [HERO]" if p.get("is_likely_hero") else ""
            console.print(f"  [{i}] {name} — {price}{hero}")
        default_choice = ",".join(
            str(i + 1) for i, p in enumerate(products[:max_products])
            if p.get("is_likely_hero")
        ) or "1"
        if auto:
            choice = default_choice
            console.print(f"\n  [dim](--auto: picking {default_choice})[/dim]")
        else:
            choice = click.prompt(
                f"\n  Which to focus on? (comma-separated, max {max_products}, or 'all')",
                default=default_choice,
            )
        if choice.strip().lower() == "all":
            chosen_products = products[:max_products]
        else:
            indices = [int(i.strip()) - 1 for i in choice.split(",") if i.strip().isdigit()]
            chosen_products = [products[i] for i in indices if 0 <= i < len(products)][:max_products]

        for product in chosen_products:
            pname = _flatten(product.get("name", "?"))
            console.print(f"\n  [cyan]Follow-ups for: {pname}[/cyan]")
            if not auto:
                mech = click.prompt("    Unique mechanism / why it works (or 'skip')", default="skip")
                if mech.lower() != "skip":
                    product["_unique_mechanism"] = mech
                benefits = click.prompt("    Top 3 benefits, comma-separated (or 'skip')", default="skip")
                if benefits.lower() != "skip":
                    product["_benefits"] = [b.strip() for b in benefits.split(",")]

    console.print("\n[bold magenta]CUSTOMER AVATAR[/bold magenta] (site can't tell us this — please share):")
    signals = data.get("avatar_signals", {}) or {}
    if signals:
        console.print(f"  [dim]Site signals: {signals.get('inferred_demographic', '?')}[/dim]")

    auto_pains: list[str] = []
    auto_desires: list[str] = []
    auto_objections: list[str] = []
    auto_triggers: list[str] = []

    if auto:
        demo = str(signals.get("inferred_demographic", ""))
        psycho = ""
        aware = signals.get("inferred_awareness_level", "problem_aware")
        # Use the raw lists directly — comma-splitting breaks on punctuation inside sentences.
        auto_pains = [p for p in signals.get("inferred_pain_points", []) if p]
        auto_desires = [d for d in signals.get("inferred_desires", []) if d]
        auto_objections = [o for o in signals.get("inferred_objections", []) if o]
        auto_triggers = [t for t in signals.get("inferred_trigger_events", []) if t]
        pain_input = desire_input = objection_input = trigger_input = "skip"
        console.print("  [dim](--auto: using signals from research)[/dim]")
    else:
        demo = click.prompt(
            "  Demographic (age, gender, location, income)",
            default=str(signals.get("inferred_demographic", "")),
        )
        psycho = click.prompt(
            "  Psychographic (values, lifestyle — 1-2 sentences)", default=""
        )
        aware = click.prompt(
            "  Awareness level",
            type=click.Choice(
                ["unaware", "problem_aware", "solution_aware", "product_aware", "most_aware"]
            ),
            default=signals.get("inferred_awareness_level", "problem_aware"),
        )
        pain_input = click.prompt("  Top 3 pain points, comma-separated (or 'skip')", default="skip")
        desire_input = click.prompt("  Top desires, comma-separated (or 'skip')", default="skip")
        objection_input = click.prompt("  Top objections, comma-separated (or 'skip')", default="skip")
        trigger_input = click.prompt("  Trigger events that make them buy (or 'skip')", default="skip")

    brand_data = data.get("brand", {})
    brand_yaml = {
        "name": _flatten(brand_data.get("name", {"value": ""})),
        "colors": {
            "primary": "",  # Fill in manually — research no longer extracts these
            "secondary": "",
            "background": "#FFFFFF",
        },
        "typography": {
            "heading": _flatten(brand_data.get("typography", {}).get("heading", {"value": ""})),
            "body": _flatten(brand_data.get("typography", {}).get("body", {"value": ""})),
        },
        "visual_identity": brand_data.get("visual_identity") or {},
        "tone": _flatten(brand_data.get("tone", {"value": ""})),
        "audience": {
            "age_range": _flatten(brand_data.get("audience", {}).get("age_range", {"value": ""})),
            "gender": _flatten(brand_data.get("audience", {}).get("gender", {"value": ""})),
            "interests": _flatten(brand_data.get("audience", {}).get("interests", {"value": []})),
        },
        "platforms": ["meta", "tiktok"],
        "press_mentions": _flatten(brand_data.get("press_mentions", {"value": []})),
        "social_proof": _flatten(brand_data.get("social_proof", {"value": []})),
        "founded": _flatten(brand_data.get("founded", {"value": ""})),
        "founder": _flatten(brand_data.get("founder", {"value": ""})),
        "mission": _flatten(brand_data.get("mission", {"value": ""})),
        "tagline": _flatten(brand_data.get("tagline", {"value": ""})),
    }

    if auto:
        pain_list = [
            {"pain": p, "intensity": "medium", "customer_language": [], "source": "auto_from_site_signals"}
            for p in auto_pains
        ]
        desire_list = [{"desire": d, "customer_language": []} for d in auto_desires]
        objection_list = list(auto_objections)
        trigger_list = list(auto_triggers)
    else:
        pain_list = (
            []
            if pain_input.lower() == "skip"
            else [
                {"pain": p.strip(), "intensity": "medium", "customer_language": [], "source": "research_interview"}
                for p in pain_input.split(",") if p.strip()
            ]
        )
        desire_list = (
            []
            if desire_input.lower() == "skip"
            else [
                {"desire": d.strip(), "customer_language": []}
                for d in desire_input.split(",") if d.strip()
            ]
        )
        objection_list = (
            [] if objection_input.lower() == "skip"
            else [o.strip() for o in objection_input.split(",") if o.strip()]
        )
        trigger_list = (
            [] if trigger_input.lower() == "skip"
            else [t.strip() for t in trigger_input.split(",") if t.strip()]
        )

    avatar_yaml = {
        "name": "Auto-drafted — please review and rename",
        "demographic": demo,
        "psychographic": psycho,
        "pain_points": pain_list,
        "desires": desire_list,
        "objections": objection_list,
        "trigger_events": trigger_list,
        "awareness_level": aware,
        "language_patterns": [],
        "current_solutions": [],
    }

    console.print("\n[bold]READY TO WRITE:[/bold]")
    console.print(f"  brand.yaml ({len([k for k in brand_yaml if brand_yaml[k]])} populated fields)")
    console.print(f"  {len(chosen_products)} product YAML(s)")
    console.print(f"  avatar.yaml (DRAFT — please review)")

    if not auto and not click.confirm(f"\nWrite to clients/{client}/?", default=True):
        console.print("[yellow]Aborted, no files written.[/yellow]")
        raise SystemExit(0)

    (client_dir / "brand.yaml").write_text(
        _yaml.dump(brand_yaml, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    products_dir = client_dir / "products"
    products_dir.mkdir(exist_ok=True)
    written_products = []
    for product in chosen_products:
        pname = _flatten(product.get("name", "untitled"))
        slug = _slugify(pname)
        prod_yaml = {
            "name": pname,
            "description": _flatten(product.get("description", {"value": ""})),
            "price": str(_flatten(product.get("price", {"value": ""}))),
            "category": "general",
            "image_path": "",
            "image_url": _flatten(product.get("image_url", {"value": ""})),
            "url": _flatten(product.get("url", {"value": ""})),
            "unique_mechanism": product.get("_unique_mechanism", ""),
            "benefits": product.get("_benefits", []),
            "objections": [],
            "social_proof": [],
        }
        path = products_dir / f"{slug}.yaml"
        path.write_text(_yaml.dump(prod_yaml, sort_keys=False, allow_unicode=True), encoding="utf-8")
        written_products.append(slug)

    (client_dir / "avatar.yaml").write_text(
        _yaml.dump(avatar_yaml, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    console.print(f"\n[green]Wrote brand.yaml, {len(written_products)} product(s), and avatar.yaml.[/green]")
    console.print("\n[bold]Next steps:[/bold]")
    console.print(f"  1. Review the files: clients/{client}/brand.yaml, avatar.yaml, products/")
    console.print(f"  2. Add customer reviews to clients/{client}/voc/ (.json or .txt)")
    console.print(f"  3. adc mine-voc --client {client} --category <category>")
    console.print(f"  4. adc voc-to-avatar --client {client} --apply  (after reviewing the VOC)")
    if written_products:
        console.print(f"  5. adc brief --client {client} --product {written_products[0]} --angles 6")


# ─── Strategy: Brief Generation ─────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--product", required=True, help="Product slug")
@click.option("--angles", default=5, help="Number of messaging angles to generate")
@click.option("--platform", default="meta", help="Target platform: meta, tiktok")
@click.option(
    "--avatar",
    "avatar_name",
    default=None,
    help="Specific avatar to use (e.g. 'primary'). Loads from "
    "clients/<slug>/avatars/<name>.yaml. Omit to use primary.yaml then fall "
    "back to legacy clients/<slug>/avatar.yaml.",
)
@click.option(
    "--ignore-psychology",
    is_flag=True,
    default=False,
    help="Skip the avatar's psychology_profile guardrails (filter + prompt block). "
    "Useful for before/after comparison.",
)
@click.option(
    "--ignore-voice",
    is_flag=True,
    default=False,
    help="Skip the brand voice bank. Hooks then come from raw avatar language "
    "instead of the unspoken-truths bank in voice.yaml. Useful for before/after "
    "comparison.",
)
@click.option(
    "--no-trending",
    "no_trending",
    is_flag=True,
    default=False,
    help="Skip the trending-format recommender. By default, every brief gets "
    "top-3 trending alternatives attached (from trending_formats.yaml).",
)
def brief(
    client: str,
    product: str,
    angles: int,
    platform: str,
    avatar_name: str | None,
    ignore_psychology: bool,
    ignore_voice: bool,
    no_trending: bool,
):
    """Generate creative briefs with messaging angles for a product.

    Layers automatically applied when present:
      - Psychology profile (if avatar has one) -> filters slots + injects guardrails
      - Competitive gap map (if competitive-gaps.yaml exists) -> biases angles to exploit gaps
      - Brand voice bank (if voice.yaml exists) -> hooks drawn from the unspoken-truths bank (--ignore-voice to skip)
    """
    import math
    import yaml as _yaml
    from models.avatar import CustomerAvatar
    from models.loader import (
        load_brand,
        load_product,
        load_avatar as _load_legacy_avatar,
        load_all_avatars,
        load_winning_patterns,
        save_brief,
    )
    from strategy.brief_generator import generate_briefs

    with console.status("Loading client data..."):
        brand = load_brand(client)
        prod = load_product(client, product)
        patterns = load_winning_patterns(client)

        # Resolve avatars:
        #   --avatar X        -> use exactly that one
        #   (no flag)         -> use ALL avatars in clients/<slug>/avatars/,
        #                         distributing the requested brief count across
        #                         them. Falls back to legacy avatar.yaml if no
        #                         avatars/ folder exists.
        avatars: list[CustomerAvatar] = []
        avatar_source = ""
        if avatar_name:
            apath = Path("clients") / client / "avatars" / f"{avatar_name}.yaml"
            if not apath.exists():
                console.print(f"[red]Avatar '{avatar_name}' not found at {apath}[/red]")
                raise SystemExit(1)
            with open(apath, encoding="utf-8") as fh:
                avatars = [CustomerAvatar(**_yaml.safe_load(fh))]
            avatar_source = str(apath)
        else:
            avatars = load_all_avatars(client)
            if avatars:
                names = ", ".join(a.name or "?" for a in avatars)
                avatar_source = f"clients/{client}/avatars/ ({len(avatars)}: {names})"
            else:
                legacy = _load_legacy_avatar(client)
                if legacy:
                    avatars = [legacy]
                    avatar_source = f"clients/{client}/avatar.yaml (legacy)"

    if not avatars:
        console.print(
            f"[yellow]No avatar found for '{client}'. "
            f"Run 'adc mine-voc' or create clients/{client}/avatar.yaml[/yellow]"
        )
        raise SystemExit(1)

    console.print(f"[dim]Avatar source: {avatar_source}[/dim]")
    use_profile = not ignore_psychology

    # Distribute `angles` briefs across avatars as evenly as possible. With
    # 9 angles across 4 avatars: ceil(9/4)=3 per avatar generated, then the
    # combined list is truncated to 9. Extras land on the higher-priority
    # avatars (primary first), which is the order load_all_avatars returns.
    per_avatar = math.ceil(angles / len(avatars))

    # Mental-stage distribution: compute ONCE across the full `angles` total
    # using the primary (first-listed) avatar's awareness as the bias center,
    # then slice per avatar. This way an --angles 6 run across 6 avatars
    # produces 6 briefs that span trigger / exploration / evaluation /
    # purchase, instead of 6 trigger briefs (one per avatar from
    # distribute_across_stages(count=1, ...)).
    from strategy.awareness_mapper import (
        classify_awareness as _classify_awareness,
        distribute_across_stages as _distribute_across_stages,
    )
    full_stage_plan = _distribute_across_stages(
        per_avatar * len(avatars),
        _classify_awareness(avatars[0]),
    )

    briefs: list = []
    for av_idx, av in enumerate(avatars):
        if av.psychology_profile and use_profile:
            n_dom = len(av.psychology_profile.dominant_heuristics)
            n_pairings = len(av.psychology_profile.recommended_prompt_pairings)
            console.print(
                f"[dim]  {av.name}: psychology profile applied "
                f"({n_dom} heuristics, {n_pairings} pairings).[/dim]"
            )
        elif av.psychology_profile and ignore_psychology:
            console.print(
                f"[yellow]  {av.name}: profile present but --ignore-psychology was set.[/yellow]"
            )
        else:
            console.print(
                f"[yellow]  {av.name}: no psychology profile. "
                f"Run `adc profile-psychology --client {client} --avatar {av.name}` for heuristic-aware angles.[/yellow]"
            )

        avatar_stages = full_stage_plan[
            av_idx * per_avatar : (av_idx + 1) * per_avatar
        ]
        with console.status(f"Generating {per_avatar} brief(s) for {av.name}..."):
            try:
                avatar_briefs = generate_briefs(
                    client_slug=client,
                    product=prod,
                    brand=brand,
                    avatar=av,
                    count=per_avatar,
                    platform=platform,
                    winning_patterns=patterns,
                    use_profile=use_profile,
                    use_voice=not ignore_voice,
                    include_trending=not no_trending,
                    mental_stages=avatar_stages,
                )
            except ValueError as e:
                console.print(f"[red]{e}[/red]")
                raise SystemExit(1)
        briefs.extend(avatar_briefs)

    # Truncate to the requested count so 9 angles across 4 avatars yields
    # exactly 9 briefs, not 12 (4 × ceil(9/4)).
    briefs = briefs[:angles]

    table = Table(title=f"Creative Briefs - {brand.name} / {prod.name}")
    table.add_column("#", style="dim")
    table.add_column("Persona", style="bold magenta", max_width=24)
    table.add_column("Hook", style="cyan", max_width=50)
    table.add_column("Angle", style="green", max_width=30)
    table.add_column("Framework", style="yellow")
    table.add_column("Brief ID", style="dim")

    for i, b in enumerate(briefs, 1):
        save_brief(client, b)
        table.add_row(str(i), b.persona or "—", b.hook, b.angle, b.framework.value, b.brief_id)

    console.print(table)
    console.print(f"\n[green]Saved {len(briefs)} briefs to clients/{client}/briefs/[/green]")
    console.print(f"\n[green]Next:[/green] adc menu --client {client}")

    from strategy.cost_tracker import log_cost
    log_cost(client, "adc brief", note=f"{len(briefs)} briefs across {len(avatars)} avatar(s) for {product}")


# ─── Drive Asset Ingestion (Phase A) ─────────────────────────────────────────


@cli.command(name="enrich-brand")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--apply/--dry-run",
    default=False,
    help="--dry-run (default) prints the proposed diff only; --apply writes brand.yaml.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Bypass the Drive-modifiedTime cache and re-run vision on every asset.",
)
@click.option(
    "--no-backup",
    is_flag=True,
    default=False,
    help="Skip writing a .yaml.bak before overwriting brand.yaml.",
)
def enrich_brand(client: str, apply: bool, force: bool, no_backup: bool):
    """Pull `brand/` assets from the client's Drive folder, vision-analyze, merge into brand.yaml.

    Reads `drive_folder_id` from brand.yaml, then ingests the `brand/` subfolder
    of that Drive folder (images via Gemini multi-image vision, PDFs via
    pdftotext + page-image vision). Defaults to dry-run with a diff preview;
    pass `--apply` to commit changes.
    """
    from strategy.brand_enricher import enrich_brand_from_drive

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    backup = not no_backup
    with console.status(f"Pulling brand assets from Drive for '{client}'..."):
        try:
            result = enrich_brand_from_drive(
                client_slug=client,
                apply=apply,
                force=force,
                backup=backup,
            )
        except (ValueError, EnvironmentError, FileNotFoundError) as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    _render_enrichment_summary(client, result, apply=apply)


def _render_enrichment_summary(client: str, result, *, apply: bool):
    """Print the proposed diff and run statistics."""
    console.print()
    console.print(
        f"[dim]images analyzed: {result.images_analyzed}  "
        f"pdfs analyzed: {result.pdfs_analyzed}  "
        f"cache hits: {result.cache_hits}  "
        f"skipped: {len(result.skipped)}[/dim]"
    )

    for filename, reason in result.skipped:
        console.print(f"[yellow]  skipped {filename}: {reason}[/yellow]")

    if not result.changes:
        console.print("[green]No proposed changes — brand.yaml already reflects Drive assets.[/green]")
        return

    console.print(f"\n[bold]Proposed {len(result.changes)} change(s) to brand.yaml:[/bold]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Field", style="cyan", max_width=32)
    table.add_column("Before", style="dim", max_width=50)
    table.add_column("After", style="green", max_width=50)
    for change in result.changes:
        before_str = _format_field_value(change.before)
        after_str = _format_field_value(change.after)
        table.add_row(change.path, before_str, after_str)
    console.print(table)

    if apply:
        console.print(
            f"\n[bold green]Applied. Wrote clients/{client}/brand.yaml "
            f"(backup at brand.yaml.bak).[/bold green]"
        )
    else:
        console.print(
            f"\n[yellow]Dry run — no changes written.[/yellow]\n"
            f"Re-run with --apply to commit: "
            f"[bold]adc enrich-brand --client {client} --apply[/bold]"
        )


def _format_field_value(value) -> str:
    """Format a brand-field value for table display."""
    if isinstance(value, list):
        if not value:
            return "[]"
        joined = ", ".join(str(v) for v in value[:3])
        if len(value) > 3:
            joined += f", +{len(value) - 3} more"
        return joined
    if value == "":
        return "(empty)"
    return str(value)[:200]


@cli.command(name="analyze-references")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--local-dir",
    "local_dir",
    default=None,
    help="Path to a LOCAL folder of reference ads (PNG/JPG/WebP/MP4/MOV/WebM). "
    "Bypasses Google Drive entirely — no auth needed. Files are copied to "
    "clients/<slug>/reference_ads/raw/ and analyzed in place.",
)
@click.option(
    "--drive-folder-id",
    "drive_folder_id",
    default=None,
    help="Arbitrary Drive folder ID to ingest. Walks its IMMEDIATE SUBFOLDERS "
    "as style buckets (e.g., editorial/ + ugc/) and preserves that grouping "
    "in clients/<slug>/reference_ads/raw/<style>/. Requires "
    "GOOGLE_APPLICATION_CREDENTIALS + the folder shared with the service "
    "account. Overrides the legacy brand.drive_folder_id / reference-ads "
    "default.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Bypass cache and re-run vision on every reference ad.",
)
def analyze_references(client: str, local_dir: str | None,
                       drive_folder_id: str | None, force: bool):
    """Vision-analyze reference ads.

    Three source modes (in priority order):
      --local-dir <path>         Local folder (no Drive auth needed).
      --drive-folder-id <id>     Arbitrary Drive folder; walks subfolders as styles.
      (default)                  Legacy: brand.drive_folder_id / reference-ads/ subfolder.

    Static images go straight to vision. Videos have a representative frame
    extracted via ffmpeg, then vision runs on that frame. Output lives at
    clients/<slug>/reference_ads/analyses/, with a _summary.yaml index.
    """
    from strategy.reference_ads import (
        analyze_references_from_drive,
        analyze_references_from_drive_folder,
        analyze_references_from_local_dir,
    )

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    if local_dir:
        src_label = f"local folder {local_dir}"
    elif drive_folder_id:
        src_label = f"Drive folder {drive_folder_id} (style-subfolder mode)"
    else:
        src_label = "Drive reference-ads/ (legacy mode)"

    with console.status(f"Analyzing reference ads for '{client}' from {src_label} via Gemini vision..."):
        try:
            if local_dir:
                result = analyze_references_from_local_dir(
                    client_slug=client,
                    local_dir=Path(local_dir),
                    force=force,
                )
                _render_references_summary(client, result)
                from strategy.cost_tracker import log_cost
                log_cost(client, "adc analyze-references", multiplier=result.new_analyses,
                         note=f"{result.new_analyses} new, {result.cache_hits} cached (local-dir)")
                return

            if drive_folder_id:
                result = analyze_references_from_drive_folder(
                    client_slug=client,
                    drive_folder_id=drive_folder_id,
                    force=force,
                )
                _render_references_summary(client, result)
                from strategy.cost_tracker import log_cost
                log_cost(client, "adc analyze-references", multiplier=result.new_analyses,
                         note=f"{result.new_analyses} new, {result.cache_hits} cached (drive-folder)")
                return

            # Fallthrough → Drive legacy path below
            result = analyze_references_from_drive(client_slug=client, force=force)
        except (ValueError, EnvironmentError, FileNotFoundError) as e:
            console.print(f"[red]{e}[/red]")
            raise SystemExit(1)

    _render_references_summary(client, result)


def _render_references_summary(client: str, result):
    """Print compact analyzed-ad table."""
    console.print()
    console.print(
        f"[dim]analyzed: {len(result.analyses)}  "
        f"new: {result.new_analyses}  "
        f"cache hits: {result.cache_hits}  "
        f"skipped: {len(result.skipped)}[/dim]"
    )
    for name, reason in result.skipped:
        console.print(f"[yellow]  skipped {name}: {reason}[/yellow]")

    if not result.analyses:
        return

    table = Table(title="Reference ad analyses", show_header=True, header_style="bold")
    table.add_column("File", style="cyan", max_width=28)
    table.add_column("Fmt", style="dim", max_width=6)
    table.add_column("Hook type", style="green", max_width=18)
    table.add_column("Visual format", style="yellow", max_width=16)
    table.add_column("Mechanic", style="magenta", max_width=28)
    table.add_column("Mood", style="dim", max_width=24)

    for a in result.analyses:
        p = a.payload
        fmt = "video" if a.is_video_frame else "img"
        mood = ", ".join((p.get("mood") or [])[:3])
        table.add_row(
            a.filename[:26],
            fmt,
            (p.get("hook_type") or "")[:18],
            (p.get("visual_format") or "")[:16],
            (p.get("creative_mechanic") or "")[:28],
            mood[:24],
        )
    console.print(table)
    console.print(
        f"\n[bold green]Wrote analyses to clients/{client}/reference_ads/analyses/[/bold green]"
    )


# ─── Psychology Profiling (Stage 1.5) ────────────────────────────────────────


@cli.command(name="profile-psychology")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--avatar",
    default=None,
    help="Specific avatar to profile (e.g. 'primary'). Omit to profile every avatar.",
)
@click.option(
    "--no-backup",
    is_flag=True,
    default=False,
    help="Skip writing the .yaml.bak sibling before overwriting.",
)
def profile_psychology(client: str, avatar: str | None, no_backup: bool):
    """Diagnose buyer psychology for an avatar — heuristics, valence/intensity, pairings.

    Reads the avatar + brand context + (optional) extracted VOC, runs the
    psychology-profiling skill via Sonnet 4.6, and writes a `psychology_profile`
    block into the avatar yaml in place. Downstream angle generation reads this
    to choose which psychological levers to activate.

    Run AFTER `adc mine-voc` for highest-confidence output. Without VOC the
    profiler will still run but flag confidence accordingly.
    """
    from strategy.psychology_profiler import (
        profile_all_avatars,
        profile_avatar_file,
    )

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    backup = not no_backup

    if avatar:
        avatar_path = client_dir / "avatars" / f"{avatar}.yaml"
        if not avatar_path.exists():
            legacy = client_dir / "avatar.yaml"
            if avatar in ("avatar", "default") and legacy.exists():
                avatar_path = legacy
            else:
                console.print(f"[red]Avatar '{avatar}' not found at {avatar_path}[/red]")
                raise SystemExit(1)

        with console.status(f"Profiling psychology for {avatar} with Sonnet 4.6..."):
            try:
                profile = profile_avatar_file(client, avatar_path, backup=backup)
            except ValueError as e:
                console.print(f"[red]{e}[/red]")
                raise SystemExit(1)

        _render_psychology_summary({avatar: profile})
    else:
        with console.status(f"Profiling all avatars for '{client}' with Sonnet 4.6..."):
            try:
                profiles = profile_all_avatars(client, backup=backup)
            except (FileNotFoundError, ValueError) as e:
                console.print(f"[red]{e}[/red]")
                raise SystemExit(1)
        _render_psychology_summary(profiles)

    console.print()
    console.print(
        f"[bold green]Psychology profiles written to clients/{client}/avatars/[/bold green]"
    )
    if backup:
        console.print(
            "[dim]Backups at <avatar>.yaml.bak - delete if you're happy with results.[/dim]"
        )
    console.print(
        f"\n[bold]Next:[/bold] adc brief --client {client} --product <id> --angles 6 "
        "(psychology profile auto-applies)"
    )

    from strategy.cost_tracker import log_cost
    n_profiled = 1 if avatar else len(profiles)
    log_cost(client, "adc profile-psychology", multiplier=n_profiled,
             note=f"{n_profiled} avatar(s) profiled")


def _render_psychology_summary(profiles):
    """Print a compact table of each avatar's profile.

    Rich treats `[...]` as markup, so square brackets in literal output must be
    escaped with a backslash. We use parens for quadrant/confidence labels to
    avoid the visual noise of escape sequences.
    """
    for name, profile in profiles.items():
        console.print(f"\n[bold cyan]{name}[/bold cyan]")

        if profile.emotional_position:
            ep = profile.emotional_position
            console.print(
                f"  [dim]Position:[/dim] primary ({ep.primary.valence}/{ep.primary.intensity}), "
                f"secondary ({ep.secondary.valence}/{ep.secondary.intensity})"
            )

        if profile.dominant_heuristics:
            console.print("  [dim]Dominant:[/dim]")
            for h in profile.dominant_heuristics:
                console.print(f"    ({h.confidence:>6}) {h.heuristic}")

        if profile.weak_heuristics:
            console.print("  [dim]Avoid (weak):[/dim]")
            for h in profile.weak_heuristics:
                console.print(f"    {h.heuristic}")

        if profile.recommended_prompt_pairings:
            console.print("  [dim]Recommended pairings:[/dim]")
            for p in profile.recommended_prompt_pairings:
                console.print(f"    + {p.pairing}")

        if profile.avoid_pairings:
            console.print("  [dim]Avoid pairings:[/dim]")
            for p in profile.avoid_pairings:
                console.print(f"    - {p.pairing}")


# ─── VOC Mining ──────────────────────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--category", default="general", help="Product category for analysis context")
def mine_voc(client: str, category: str):
    """Mine voice-of-customer data from reviews in clients/{client}/voc/."""
    from strategy.voc_miner import mine_voc_for_client, voc_to_avatar_fields

    with console.status(f"Mining VOC data for '{client}'..."):
        voc_data = mine_voc_for_client(client, category)

    # Display results
    pain_points = voc_data.get("pain_points", [])
    console.print(f"\n[green]Found {len(pain_points)} pain points:[/green]")
    for p in pain_points[:5]:
        if isinstance(p, dict):
            console.print(f"  [{p.get('intensity', '?')}] {p.get('pain', '')}")
            for lang in p.get("customer_language", [])[:2]:
                console.print(f"    → \"{lang}\"")

    # Save extracted data
    import yaml
    output_path = Path("clients") / client / "voc" / "extracted_pains.yaml"
    with open(output_path, "w") as f:
        yaml.dump(voc_data, f, default_flow_style=False, sort_keys=False)

    console.print(f"\n[green]Saved to {output_path}[/green]")
    console.print("Use this to update your avatar: clients/{client}/avatar.yaml")


# ─── VOC → Avatar Sync ───────────────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option("--mode", type=click.Choice(["replace", "merge"]), default="replace",
              help="replace = overwrite avatar fields with VOC; merge = append")
@click.option("--apply/--dry-run", default=False,
              help="--dry-run (default) prints diff only; --apply writes changes")
def voc_to_avatar(client: str, mode: str, apply: bool):
    """Sync extracted VOC into the client's avatar.yaml.

    Reads clients/{client}/voc/extracted_pains.yaml and updates avatar.yaml.
    Always preserves: name, demographic, psychographic, awareness_level.
    Replaces or merges: pain_points, desires, objections, trigger_events, language_patterns.

    Default is dry-run — preview the diff before committing with --apply.
    """
    import yaml as _yaml
    from models.loader import load_avatar, save_avatar
    from strategy.voc_miner import voc_to_avatar_fields

    voc_path = Path("clients") / client / "voc" / "extracted_pains.yaml"
    if not voc_path.exists():
        console.print(f"[red]No extracted VOC found at {voc_path}[/red]")
        console.print(f"Run: adc mine-voc --client {client} --category <category>")
        raise SystemExit(1)

    with open(voc_path) as f:
        voc_data = _yaml.safe_load(f)

    new_fields = voc_to_avatar_fields(voc_data)
    avatar = load_avatar(client)
    if avatar is None:
        console.print(f"[red]No existing avatar at clients/{client}/avatar.yaml[/red]")
        raise SystemExit(1)

    before = {
        "pain_points": len(avatar.pain_points),
        "desires": len(avatar.desires),
        "objections": len(avatar.objections),
        "trigger_events": len(avatar.trigger_events),
        "language_patterns": len(avatar.language_patterns),
    }

    if mode == "replace":
        avatar.pain_points = new_fields["pain_points"]
        avatar.desires = new_fields["desires"]
        avatar.objections = new_fields["objections"]
        avatar.trigger_events = new_fields["trigger_events"]
        avatar.language_patterns = new_fields["language_patterns"]
    else:  # merge — append, dedup by primary key
        existing_pains = {p.pain for p in avatar.pain_points}
        avatar.pain_points.extend(
            p for p in new_fields["pain_points"] if p.pain not in existing_pains
        )
        existing_desires = {d.desire for d in avatar.desires}
        avatar.desires.extend(
            d for d in new_fields["desires"] if d.desire not in existing_desires
        )
        avatar.objections = list(dict.fromkeys(avatar.objections + new_fields["objections"]))
        avatar.trigger_events = list(dict.fromkeys(
            avatar.trigger_events + new_fields["trigger_events"]
        ))
        avatar.language_patterns = list(dict.fromkeys(
            avatar.language_patterns + new_fields["language_patterns"]
        ))

    after = {
        "pain_points": len(avatar.pain_points),
        "desires": len(avatar.desires),
        "objections": len(avatar.objections),
        "trigger_events": len(avatar.trigger_events),
        "language_patterns": len(avatar.language_patterns),
    }

    table = Table(title=f"Avatar field counts ({mode} mode)")
    table.add_column("Field", style="cyan")
    table.add_column("Before", style="dim")
    table.add_column("After", style="green")
    table.add_column("Delta", style="yellow")
    for key in before:
        delta = after[key] - before[key]
        sign = "+" if delta >= 0 else ""
        table.add_row(key, str(before[key]), str(after[key]), f"{sign}{delta}")
    console.print(table)

    console.print("\n[cyan]Preserved (untouched):[/cyan]")
    console.print(f"  name: {avatar.name}")
    console.print(f"  demographic: {avatar.demographic[:80]}...")
    console.print(f"  awareness_level: {avatar.awareness_level}")

    if apply:
        path = save_avatar(client, avatar, backup=True)
        console.print(f"\n[green]Wrote {path}[/green]")
        console.print(f"[dim]Backup at {path.with_suffix('.yaml.bak')}[/dim]")
    else:
        console.print("\n[yellow]Dry run — no changes written.[/yellow]")
        console.print(f"Re-run with --apply to write changes to clients/{client}/avatar.yaml")


# ─── Performance Feedback Loop ───────────────────────────────────────────────


@cli.command(name="creative-matrix")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--tier",
    type=click.Choice(["lean", "standard", "wide"]),
    default="standard",
    help="Cost tier: lean (trending_match only), standard (match + next), "
    "wide (match + alternates — currently same as standard).",
)
@click.option(
    "--tab",
    type=click.Choice(["pain", "love", "wish", "hook", "all"]),
    default="all",
    help="Which tab to (re)generate. 'all' = full rebuild.",
)
@click.option(
    "--row",
    default=None,
    help="Operate on a single row by ID (e.g. pain-001). Used with --next or "
    "for inspecting/refreshing a single row's trending match.",
)
@click.option(
    "--next",
    "next_idea",
    is_flag=True,
    help="With --row: lazily generate trending_next for the row. With no --row: "
    "fill trending_next for every row missing one (post-lean upgrade).",
)
@click.option(
    "--refresh-trending",
    is_flag=True,
    help="Re-run only the trending-match step for every row, keep strategic "
    "columns intact. Cheap way to refresh after editing trending_formats.yaml.",
)
@click.option(
    "--no-xlsx",
    is_flag=True,
    help="Skip writing the XLSX workbook (YAML still written).",
)
def creative_matrix_cmd(
    client: str,
    tier: str,
    tab: str,
    row: str | None,
    next_idea: bool,
    refresh_trending: bool,
    no_xlsx: bool,
):
    """Build the tactical creative matrix — 4 tabs of combinatorial ad seeds.

    Outputs:
      clients/<slug>/strategy/creative_matrix.yaml   (machine-readable)
      clients/<slug>/strategy/creative_matrix.xlsx   (human-facing)
      clients/<slug>/strategy/matrix_meta.yaml       (provenance)

    Typical workflow:
      adc creative-matrix --client secondkind                       # full build
      adc creative-matrix --client secondkind --tab pain            # regen tab 1
      adc creative-matrix --client secondkind --row pain-001 --next # lazy next
      adc creative-matrix --client secondkind --refresh-trending    # re-match
    """
    from models.matrix import MatrixTab
    from strategy.creative_matrix import (
        fill_trending_next_for_row,
        generate_matrix,
        load_matrix,
        matrix_paths,
        refresh_all_trending,
        regenerate_tab,
        save_matrix,
    )
    from strategy.matrix_xlsx import write_matrix_xlsx
    from strategy.trending import load_trending_formats

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    tab_enum_map = {
        "pain": MatrixTab.PAIN_VS_COMPETITOR,
        "love": MatrixTab.WHAT_THEY_LOVE,
        "wish": MatrixTab.WISHES_GAPS,
        "hook": MatrixTab.HOOK_ANGLES,
    }

    # ─── Single-row operations ─────────────────────────────────────────────
    if row is not None:
        matrix = load_matrix(client)
        target = matrix.find_row(row)
        if target is None:
            console.print(f"[red]Row '{row}' not found in matrix.[/red]")
            raise SystemExit(1)


        if next_idea:
            with console.status(f"Generating trending_next for {row}..."):
                next_obj = fill_trending_next_for_row(target)
            if next_obj is None:
                console.print(f"[yellow]Could not generate trending_next for {row}.[/yellow]")
                raise SystemExit(1)
            target.trending_next = next_obj
            matrix.replace_row(target)
            save_matrix(matrix, client)
            console.print(f"[green]Filled trending_next for {row}.[/green]")
            console.print(f"  idea: {next_obj.idea}")
            console.print(f"  principle: {next_obj.principle}")
            console.print(f"  execution: {next_obj.execution[:120]}...")
        else:
            console.print(f"[cyan]Row {row}:[/cyan]")
            console.print(target.model_dump_json(indent=2))
        return

    # ─── Refresh trending only ─────────────────────────────────────────────
    if refresh_trending:
        matrix = load_matrix(client)
        with console.status("Refreshing trending matches for every row..."):
            refresh_all_trending(matrix)
        save_matrix(matrix, client)
        if not no_xlsx:
            write_matrix_xlsx(matrix, matrix_paths(client)["xlsx"])
        console.print(
            f"[green]Refreshed trending matches across "
            f"{len(matrix.all_rows())} rows.[/green]"
        )
        return

    # ─── Bulk fill missing trending_next (post-lean upgrade) ───────────────
    if next_idea and tab == "all":
        matrix = load_matrix(client)
        formats = load_trending_formats()
        filled = 0
        for r in matrix.all_rows():
            if r.trending_next is not None:
                continue
            next_obj = fill_trending_next_for_row(r, formats=formats)
            if next_obj is not None:
                r.trending_next = next_obj
                filled += 1
        save_matrix(matrix, client)
        if not no_xlsx:
            write_matrix_xlsx(matrix, matrix_paths(client)["xlsx"])
        console.print(f"[green]Filled trending_next on {filled} row(s).[/green]")
        return

    # ─── Single tab regenerate ─────────────────────────────────────────────
    if tab != "all":
        matrix = load_matrix(client)
        tab_enum = tab_enum_map[tab]
        with console.status(f"Regenerating tab {tab_enum.value}..."):
            regenerate_tab(matrix, tab_enum, client)
        save_matrix(matrix, client)
        if not no_xlsx:
            write_matrix_xlsx(matrix, matrix_paths(client)["xlsx"])
        new_rows = getattr(matrix, tab_enum.value)
        console.print(
            f"[green]Regenerated {tab_enum.value} ({len(new_rows)} rows).[/green]"
        )
        return

    # ─── Full build ────────────────────────────────────────────────────────
    console.print(
        f"\n[bold cyan]Building creative matrix for {client}[/bold cyan] (tier={tier})"
    )
    with console.status("Synthesizing 4 tabs + trending matches with Claude..."):
        matrix = generate_matrix(client, tier=tier)
    save_matrix(matrix, client)

    if not no_xlsx:
        xlsx_path = write_matrix_xlsx(matrix, matrix_paths(client)["xlsx"])
        console.print(f"[green]XLSX:[/green] {xlsx_path}")

    paths = matrix_paths(client)
    console.print(f"[green]YAML:[/green] {paths['yaml']}")
    console.print(f"[green]Meta:[/green] {paths['meta']}")

    table = Table(title=f"Creative Matrix — {client}")
    table.add_column("Tab", style="cyan")
    table.add_column("Rows", style="green")
    table.add_column("With trending_match", style="yellow")
    table.add_column("With trending_next", style="dim")
    for tab_enum in [
        MatrixTab.PAIN_VS_COMPETITOR,
        MatrixTab.WHAT_THEY_LOVE,
        MatrixTab.WISHES_GAPS,
        MatrixTab.HOOK_ANGLES,
    ]:
        rows = getattr(matrix, tab_enum.value)
        with_match = sum(1 for r in rows if r.trending_match)
        with_next = sum(1 for r in rows if r.trending_next)
        table.add_row(
            tab_enum.value,
            str(len(rows)),
            str(with_match),
            str(with_next),
        )
    console.print(table)
    console.print(
        f"\n[dim]Tier: {tier} — "
        f"{'standard (match + next)' if tier == 'standard' else tier}.[/dim]"
    )


@cli.command()
@click.option("--text", default=None, help="Text to check")
@click.option("--brief-id", default=None, help="Brief ID to check")
@click.option("--client", default=None, help="Client slug for brand-specific rules")
@click.option("--category", multiple=True, default=["general"], help="Rule categories to check")
def check_compliance(text: str | None, brief_id: str | None, client: str | None, category: tuple):
    """Check ad copy for compliance issues."""
    from validators.compliance.scanner import scan_text, scan_brief, Severity

    if brief_id and client:
        from models.loader import load_brief
        brief_obj = load_brief(client, brief_id)
        issues = scan_brief(
            brief_obj.model_dump(),
            categories=list(category),
            client_slug=client,
        )
    elif text:
        issues = scan_text(text, categories=list(category), client_slug=client)
    else:
        console.print("[red]Provide --text or --brief-id + --client[/red]")
        return

    if not issues:
        console.print("[green]No compliance issues found.[/green]")
        return

    errors = [i for i in issues if i.severity == Severity.ERROR]
    warnings = [i for i in issues if i.severity == Severity.WARNING]

    if errors:
        console.print(f"\n[red]{len(errors)} ERROR(s):[/red]")
        for i in errors:
            console.print(f"  [red]ERROR[/red] [{i.category}] {i.rule}: matched '{i.match}'")
            console.print(f"         {i.context}")

    if warnings:
        console.print(f"\n[yellow]{len(warnings)} WARNING(s):[/yellow]")
        for i in warnings:
            console.print(f"  [yellow]WARN[/yellow]  [{i.category}] {i.rule}: matched '{i.match}'")


# ─── Check Copy Length ───────────────────────────────────────────────────────


@cli.command()
@click.option("--text", required=True, help="The ad copy text to check")
@click.option("--platform", required=True, help="Platform: meta, google, tiktok, linkedin, x")
@click.option("--field", required=True, help="Field: headline, primary_text, description, etc. (use --list-fields to see)")
@click.option("--trim/--no-trim", default=False, help="Show a trimmed suggestion if over limit")
def check_copy(text: str, platform: str, field: str, trim: bool):
    """Check ad copy text against platform char limits."""
    from validators.copy_checker import Severity, check_copy as _check, suggest_trim

    result = _check(text, platform=platform, field=field)

    icon = {
        Severity.OK: "[green]PASS[/green]",
        Severity.WARNING: "[yellow]WARN[/yellow]",
        Severity.ERROR: "[red]FAIL[/red]",
    }[result.severity]

    console.print(f"\n{icon} {result.platform}/{result.field}: {result.detail}")

    if not result.passed and trim:
        suggestion = suggest_trim(text, target=result.recommended)
        console.print(f"\n[cyan]Trimmed to {result.recommended} chars:[/cyan]")
        console.print(f"  {suggestion}")


@cli.command()
def list_copy_specs():
    """List all platforms and fields with their char limits."""
    from validators.copy_checker import PLATFORM_LIMITS, list_platforms

    for platform in list_platforms():
        table = Table(title=f"{platform.upper()} ad copy limits")
        table.add_column("Field", style="cyan")
        table.add_column("Recommended", style="green")
        table.add_column("Hard max", style="yellow")
        for field, limits in PLATFORM_LIMITS[platform].items():
            hard = str(limits["hard_max"]) if limits["hard_max"] is not None else "—"
            table.add_row(field, str(limits["recommended"]), hard)
        console.print(table)


# ─── Validate Image ──────────────────────────────────────────────────────────


@cli.command()
@click.option("--port", default=8501, type=int, help="Local port (default 8501)")
@click.option("--public", is_flag=True, default=False,
              help="Bind to 0.0.0.0 so other devices on your LAN can reach it. "
              "Default binds to localhost only (safer).")
def dashboard(port: int, public: bool):
    """Launch the interactive web dashboard (Streamlit).

    Opens in your browser at http://localhost:<port>/. Reads the same files
    the `adc status` command reads — no API calls, no data leaves your machine.

    Stop with Ctrl-C.
    """
    import subprocess

    repo_root = Path(__file__).resolve().parent
    app_path = repo_root / "dashboard" / "app.py"
    if not app_path.exists():
        console.print(f"[red]Dashboard app not found at {app_path}[/red]")
        raise SystemExit(1)

    address = "0.0.0.0" if public else "localhost"
    console.print(
        f"[green]Launching dashboard at http://{address}:{port}/[/green]\n"
        f"[dim]Press Ctrl-C to stop.[/dim]\n"
    )
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app_path),
        "--server.port", str(port),
        "--server.address", address,
        "--server.headless", "false",
        "--browser.gatherUsageStats", "false",
    ]
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


# ─── Client Status Dashboard ────────────────────────────────────────────────


@cli.command()
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--save",
    is_flag=True,
    default=False,
    help="Also write the dashboard to clients/<slug>/STATUS.md for sharing.",
)
def status(client: str, save: bool):
    """Dashboard view: what's done for a client, what to run next.

    Pure local-file inspection — free, fast (<1s), no API calls.
    """
    from strategy.status_dashboard import (
        ad_assets_status,
        build_recommendations,
        competitive_research_status,
        strategy_status,
    )

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    strategy_stages = strategy_status(client)
    competitive_stages = competitive_research_status(client)
    asset_stages = ad_assets_status(client)
    recommendations = build_recommendations(
        client, strategy_stages, competitive_stages, asset_stages
    )

    def _render_section(title: str, stages):
        table = Table(title=title)
        table.add_column(" ", justify="center", style="bold", width=6)
        table.add_column("Stage", style="cyan", min_width=24)
        table.add_column("Details", style="green", max_width=48)
        table.add_column("Age", style="dim", justify="right")
        for s in stages:
            # Use plain words instead of [x]/[ ] — Rich treats square brackets as markup
            check = "[green]OK[/green]" if s.done else "[yellow]--[/yellow]"
            age = ""
            if s.age_days is not None:
                if s.age_days == 0:
                    age = "today"
                elif s.age_days == 1:
                    age = "1 day"
                else:
                    age = f"{s.age_days} days"
            details = s.summary
            if s.notes:
                details += f" -- {'; '.join(s.notes)}"
            table.add_row(check, s.name, details, age)
        console.print(table)

    console.print(f"\n[bold magenta]Status for client: {client}[/bold magenta]\n")
    _render_section("Strategy", strategy_stages)
    _render_section("Competitive Research", competitive_stages)
    _render_section("Ad Assets", asset_stages)

    console.print("\n[bold]Recommended next steps:[/bold]")
    for r in recommendations:
        console.print(f"  -> {r}")
    console.print()

    if save:
        from datetime import datetime as _dt
        md_lines = [
            f"# Status — {client}",
            f"_Generated {_dt.now().strftime('%Y-%m-%d %H:%M')}_",
            "",
        ]

        def _md_section(title: str, stages):
            md_lines.append(f"## {title}\n")
            md_lines.append("| Status | Stage | Details | Age |")
            md_lines.append("|---|---|---|---|")
            for s in stages:
                check = "OK" if s.done else "--"
                age = ""
                if s.age_days is not None:
                    if s.age_days == 0:
                        age = "today"
                    elif s.age_days == 1:
                        age = "1 day"
                    else:
                        age = f"{s.age_days} days"
                details = s.summary
                if s.notes:
                    details += f" — {'; '.join(s.notes)}"
                md_lines.append(
                    f"| {check} | {s.name} | {details} | {age} |"
                )
            md_lines.append("")

        _md_section("Strategy", strategy_stages)
        _md_section("Competitive Research", competitive_stages)
        _md_section("Ad Assets", asset_stages)

        md_lines.append("## Recommended next steps\n")
        for r in recommendations:
            md_lines.append(f"- {r}")

        out_path = client_dir / "STATUS.md"
        out_path.write_text("\n".join(md_lines), encoding="utf-8")
        console.print(f"[green]Saved dashboard to: {out_path}[/green]")


# ─── Competitor Research ────────────────────────────────────────────────────


@cli.command(name="research-competitors")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--force-refresh",
    is_flag=True,
    default=False,
    help="Re-run Exa queries and re-scrape competitor sites even if cached.",
)
@click.option(
    "--skip-onsite",
    is_flag=True,
    default=False,
    help="Skip competitor on-site review scraping (Exa-only run).",
)
def research_competitors(client: str, force_refresh: bool, skip_onsite: bool):
    """Pull all competitive research: on-site reviews + Exa sentiment-stratified queries."""
    from models.loader import load_brand
    from strategy.competitor_research import (
        cache_competitor_bundle,
        load_competitors,
        pull_competitor_reviews,
    )
    from strategy.exa_research import (
        cache_result,
        competitive_queries_for_brand,
        run_query,
    )

    brand = load_brand(client)
    competitors = load_competitors(client)

    if not competitors:
        console.print(
            f"[red]No competitors.yaml found for {client}.[/red]\n"
            f"[dim]Create clients/{client}/competitors.yaml first.[/dim]"
        )
        raise SystemExit(1)

    console.print(f"[cyan]Competitive research: {brand.name} vs {len(competitors)} competitors[/cyan]")
    for c in competitors:
        console.print(f"  - {c.name} ({c.priority}, {c.type}) -> {c.url}")

    console.print(
        f"\n[yellow]This run includes:[/yellow] Exa web sentiment (Reddit, Trustpilot, news) "
        f"+ on-site reviews via Firecrawl.\n"
        f"[yellow]NOT included:[/yellow] Amazon reviews. Run separately with: "
        f"[cyan]adc research-amazon --client {client}[/cyan]\n"
    )

    # 1) On-site competitor review scraping
    onsite_table = Table(title="On-site Reviews via Firecrawl")
    onsite_table.add_column("Competitor", style="cyan")
    onsite_table.add_column("Vendor", style="yellow")
    onsite_table.add_column("Reviews", justify="right", style="green")
    onsite_table.add_column("Pages tried", justify="right", style="dim")
    onsite_table.add_column("Notes", style="dim", max_width=40)

    if not skip_onsite:
        onsite_cache = Path("clients") / client / "research" / "competitor-reviews"
        for competitor in competitors:
            cache_path = onsite_cache / f"{competitor.slug}.json"
            if cache_path.exists() and not force_refresh:
                import json as _json
                data = _json.loads(cache_path.read_text(encoding="utf-8"))
                onsite_table.add_row(
                    competitor.name,
                    data.get("vendor", "?"),
                    str(len(data.get("reviews", []))),
                    str(len(data.get("scraped_pages", []))),
                    "(cached)",
                )
                continue

            with console.status(f"Scraping {competitor.name}..."):
                bundle = pull_competitor_reviews(competitor)
            cache_competitor_bundle(client, bundle)
            onsite_table.add_row(
                competitor.name,
                bundle.vendor,
                str(len(bundle.reviews)),
                str(len(bundle.scraped_pages)),
                bundle.notes[:80],
            )
        console.print(onsite_table)

    # 2) Exa sentiment-stratified queries
    queries = competitive_queries_for_brand(
        own_brand=brand.name,
        competitor_names=[c.name for c in competitors],
    )

    exa_table = Table(title="Exa Web Sentiment")
    exa_table.add_column("#", justify="right", style="dim")
    exa_table.add_column("Label", style="cyan")
    exa_table.add_column("Category", style="yellow")
    exa_table.add_column("Hits", justify="right", style="green")
    exa_table.add_column("Top domain", style="dim")

    exa_cache = Path("clients") / client / "research" / "exa" / "raw"
    for i, q in enumerate(queries, 1):
        from strategy.exa_research import _slugify
        cache_path = exa_cache / f"{_slugify(q.label)}.json"
        if cache_path.exists() and not force_refresh:
            import json as _json
            data = _json.loads(cache_path.read_text(encoding="utf-8"))
            top_domain = data["results"][0]["domain"] if data.get("results") else "-"
            exa_table.add_row(
                str(i), q.label, q.category, str(len(data.get("results", []))), top_domain
            )
            continue

        try:
            with console.status(f"Exa: {q.label}..."):
                result = run_query(q)
            cache_result(client, result)
            top_domain = result.results[0].domain if result.results else "-"
            exa_table.add_row(
                str(i), q.label, q.category, str(len(result.results)), top_domain
            )
        except Exception as e:
            exa_table.add_row(
                str(i), q.label, q.category, "ERROR", str(e)[:40]
            )

    console.print(exa_table)
    console.print(
        f"\n[green]Cached competitor reviews: clients/{client}/research/competitor-reviews/[/green]\n"
        f"[green]Cached Exa results: clients/{client}/research/exa/raw/[/green]\n\n"
        f"[bold]Recommended next steps:[/bold]\n"
        f"  - [cyan]adc research-amazon --client {client}[/cyan]   "
        f"(pull Amazon reviews; ~$1-5)\n"
        f"  - [cyan]adc analyze-gaps --client {client}[/cyan]      "
        f"(synthesize what's cached so far; ~$1.50)"
    )

    from strategy.cost_tracker import log_cost
    log_cost(client, "adc research-competitors",
             note=f"{len(competitors)} competitor(s), {len(queries)} Exa queries")


@cli.command(name="research-amazon")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--max-reviews", default=100, type=int,
    help="Max reviews per Amazon product per star tier (default 100). "
    "On the Apify free tier, each call is capped at ~8 reviews anyway.",
)
@click.option(
    "--stars", default="5,3,1",
    help="Comma-separated star tiers to pull (default '5,3,1' matching the gap "
    "analysis framework). Use '5,4,3,2,1' for full stratification, or '0' for "
    "no filter (returns recent reviews only).",
)
@click.option(
    "--force-refresh", is_flag=True, default=False,
    help="Re-scrape Amazon even if cached.",
)
def research_amazon(client: str, max_reviews: int, stars: str, force_refresh: bool):
    """Scrape Amazon reviews stratified by star rating for each competitor.

    Reads amazon_urls from clients/<slug>/competitors.yaml. Each star tier is a
    separate Apify call — on the free plan this multiplies per-product review
    yield by the number of tiers (typically 3 = 5/3/1 star).
    """
    from strategy.apify_amazon import (
        DEFAULT_STAR_FILTERS,
        STAR_FILTER_SHORT_NAMES,
        _extract_asin,
        cache_amazon_bundle,
        scrape_amazon_reviews,
    )
    from strategy.competitor_research import load_competitors

    # Map user input to Apify's filter values
    star_map = {
        "5": "five_star", "4": "four_star", "3": "three_star",
        "2": "two_star", "1": "one_star", "0": "all_stars",
    }
    star_filters: list[str] = []
    for s in stars.split(","):
        s = s.strip()
        if s in star_map:
            star_filters.append(star_map[s])
        else:
            console.print(f"[red]Invalid star value: '{s}'. Use 1-5 or 0 for no filter.[/red]")
            raise SystemExit(1)
    if not star_filters:
        star_filters = DEFAULT_STAR_FILTERS

    competitors = load_competitors(client)
    if not competitors:
        console.print(f"[red]No competitors.yaml found for {client}.[/red]")
        raise SystemExit(1)

    targets = [(c, url) for c in competitors for url in (c.amazon_urls or [])]
    if not targets:
        console.print(
            f"[yellow]No amazon_urls set in clients/{client}/competitors.yaml.[/yellow]\n"
            f"[dim]Add an `amazon_urls:` list to each competitor with 1-3 product URLs, then re-run.[/dim]"
        )
        raise SystemExit(1)

    total_calls = len(targets) * len(star_filters)
    console.print(
        f"[cyan]Amazon Reviews via Apify[/cyan] — {len(targets)} product(s) "
        f"x {len(star_filters)} star tier(s) = {total_calls} call(s)"
    )
    console.print(
        f"[dim]Actor: junglee/amazon-reviews-scraper. Star tiers: "
        f"{', '.join(star_filters)}. Free tier yields ~8 reviews/call (~{8 * total_calls} total).[/dim]\n"
    )

    cache_dir = Path("clients") / client / "research" / "amazon-reviews"
    table = Table(title="Amazon Review Scraping (Stratified)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Competitor", style="cyan")
    table.add_column("ASIN", style="yellow")
    table.add_column("Tier", style="magenta")
    table.add_column("Reviews", style="green", justify="right")
    table.add_column("Notes", style="dim", max_width=40)

    call_num = 0
    for competitor, url in targets:
        asin = _extract_asin(url)
        asin_part = asin or re.sub(r"[^a-zA-Z0-9]+", "-", url)[:20]
        for star_filter in star_filters:
            call_num += 1
            short = STAR_FILTER_SHORT_NAMES.get(star_filter, star_filter)
            cache_path = cache_dir / f"{competitor.slug}-{asin_part}-{short}.json"

            if cache_path.exists() and not force_refresh:
                import json as _json
                data = _json.loads(cache_path.read_text(encoding="utf-8"))
                table.add_row(
                    str(call_num),
                    competitor.name,
                    asin or "?",
                    short,
                    str(len(data.get("reviews", []))),
                    "(cached)",
                )
                continue

            with console.status(
                f"Scraping {competitor.name} {short} ({asin or 'no-ASIN'})..."
            ):
                bundle = scrape_amazon_reviews(
                    product_url=url,
                    competitor_slug=competitor.slug,
                    competitor_name=competitor.name,
                    max_reviews=max_reviews,
                    star_filter=star_filter,
                )
            cache_amazon_bundle(client, bundle)
            table.add_row(
                str(call_num),
                competitor.name,
                bundle.asin or "?",
                short,
                str(len(bundle.reviews)),
                (bundle.notes or "OK")[:40],
            )

    console.print(table)

    # Tally totals per competitor
    from collections import defaultdict
    totals: dict[str, int] = defaultdict(int)
    for path in cache_dir.glob("*.json"):
        try:
            import json as _json
            d = _json.loads(path.read_text(encoding="utf-8"))
            totals[d.get("competitor_name", "?")] += len(d.get("reviews", []))
        except Exception:
            continue
    total_all = sum(totals.values())

    console.print(
        f"\n[green]Cached to: clients/{client}/research/amazon-reviews/[/green]"
    )
    console.print(f"[bold]Review totals per competitor:[/bold]")
    for name, n in sorted(totals.items(), key=lambda x: -x[1]):
        console.print(f"  {name}: {n}")
    console.print(f"  [bold]TOTAL: {total_all} reviews[/bold]\n")
    console.print(
        f"[dim]Next: adc analyze-gaps --client {client} "
        f"(will include Amazon data, stratified by star)[/dim]"
    )

    from strategy.cost_tracker import log_cost
    log_cost(client, "adc research-amazon", multiplier=call_num,
             note=f"{call_num} call(s), {total_all} review(s)")


# ─── Tier 3 social-comment research ──────────────────────────────────────────


@cli.command(name="research-social")
@click.option("--client", required=True, help="Client slug")
@click.option("--skip-tiktok", is_flag=True, help="Skip TikTok scraping")
@click.option("--skip-instagram", is_flag=True, help="Skip Instagram scraping")
@click.option("--skip-youtube", is_flag=True, help="Skip YouTube scraping")
@click.option(
    "--max-comments",
    default=100,
    type=int,
    help="Max comments per post/video. Default 100.",
)
@click.option(
    "--max-posts",
    default=8,
    type=int,
    help="Max recent posts/videos per profile (only used when no explicit URLs/IDs "
    "are set on the competitor). Default 8.",
)
@click.option(
    "--force-refresh",
    is_flag=True,
    help="Re-scrape even when cached bundles exist for a (competitor, post) pair.",
)
def research_social(
    client: str,
    skip_tiktok: bool,
    skip_instagram: bool,
    skip_youtube: bool,
    max_comments: int,
    max_posts: int,
    force_refresh: bool,
):
    """Pull TikTok / Instagram / YouTube comments for every configured competitor.

    Reads social handles from clients/<slug>/competitors.yaml. Each competitor
    can specify any subset of:
      tiktok_handle      / tiktok_post_urls
      instagram_handle   / instagram_post_urls
      youtube_handle     / youtube_channel_id / youtube_video_ids

    Outputs:
      clients/<slug>/research/{tiktok,instagram,youtube}-comments/*.json   raw
      clients/<slug>/voc/{tiktok,instagram,youtube}-comments.json          voc-miner ready

    Cost ballpark for 3 competitors × 8 posts × 100 comments:
      TikTok    ~$2.40 + ~$1 video listing  (~$3.40)
      Instagram ~$2.40 + ~$2 post listing   (~$4.40)
      YouTube   $0 (well under free quota)

    Recommended next step after running this:
      adc mine-voc --client <slug> --category <category>
      adc analyze-gaps --client <slug>
    """
    from strategy.competitor_research import load_competitors
    from strategy.social_comments import (
        SocialCommentBundle,
        _research_dir,
        cache_bundle,
        write_voc_dump,
    )

    client_dir = Path("clients") / client
    if not client_dir.exists():
        console.print(f"[red]Client '{client}' not found at {client_dir}[/red]")
        raise SystemExit(1)

    competitors = load_competitors(client)
    if not competitors:
        console.print(f"[red]No competitors found at clients/{client}/competitors.yaml[/red]")
        raise SystemExit(1)

    console.print(
        f"\n[bold cyan]Tier 3 social research for {client}[/bold cyan] "
        f"({len(competitors)} competitor(s))"
    )

    # Build a per-(platform, competitor) plan first so we can show cost intent
    # before any network call.
    plan: list[tuple[str, object, str]] = []  # (platform, competitor, reason)
    for c in competitors:
        if not skip_tiktok and (
            c.tiktok_handle or c.tiktok_post_urls or c.tiktok_search_queries
        ):
            if c.tiktok_post_urls:
                source = f"{len(c.tiktok_post_urls)} explicit URL(s)"
            elif c.tiktok_search_queries:
                qs = ", ".join(c.tiktok_search_queries[:2])
                more = (
                    f" +{len(c.tiktok_search_queries) - 2}"
                    if len(c.tiktok_search_queries) > 2
                    else ""
                )
                source = f"search: {qs}{more}"
            else:
                source = c.tiktok_handle
            plan.append(("tiktok", c, source))
        if not skip_instagram and (c.instagram_handle or c.instagram_post_urls):
            plan.append(("instagram", c, c.instagram_handle or "explicit posts"))
        if not skip_youtube and (
            c.youtube_handle or c.youtube_channel_id or c.youtube_video_ids
        ):
            plan.append((
                "youtube",
                c,
                c.youtube_handle or c.youtube_channel_id or "explicit videos",
            ))

    if not plan:
        console.print(
            "[yellow]No competitors have social handles configured. Add "
            "tiktok_handle / instagram_handle / youtube_handle (or *_urls / *_ids) "
            "to competitors.yaml.[/yellow]"
        )
        return

    plan_table = Table(title="Scrape plan")
    plan_table.add_column("Platform", style="cyan")
    plan_table.add_column("Competitor", style="green")
    plan_table.add_column("Source", style="dim")
    for platform, c, source in plan:
        plan_table.add_row(platform, c.name, str(source))
    console.print(plan_table)

    # ─── Execute ─────────────────────────────────────────────────────────
    results_table = Table(title="Results")
    results_table.add_column("Platform", style="cyan")
    results_table.add_column("Competitor", style="green")
    results_table.add_column("Posts", justify="right", style="yellow")
    results_table.add_column("Comments", justify="right", style="yellow")
    results_table.add_column("Status", style="dim")

    platforms_touched: set[str] = set()
    total_comments = 0

    for platform, competitor, _source in plan:
        cache_dir = _research_dir(client, platform)
        already_cached = (
            any(cache_dir.glob(f"{competitor.slug}-*.json"))
            if cache_dir.exists()
            else False
        )
        if already_cached and not force_refresh:
            results_table.add_row(
                platform, competitor.name, "—", "—",
                "cached (use --force-refresh)",
            )
            platforms_touched.add(platform)
            # Count cached comments for the summary
            from strategy.social_comments import load_cached_bundles
            for b in load_cached_bundles(client, platform):
                if b.competitor_slug == competitor.slug:
                    total_comments += len(b.comments)
            continue

        bundles: list[SocialCommentBundle] = []
        status = "ok"
        try:
            if platform == "tiktok":
                from strategy.apify_tiktok import scrape_tiktok_for_competitor
                with console.status(f"TikTok: {competitor.name}..."):
                    bundles = scrape_tiktok_for_competitor(
                        competitor,
                        max_videos_per_profile=max_posts,
                        max_comments_per_video=max_comments,
                    )
            elif platform == "instagram":
                from strategy.apify_instagram import scrape_instagram_for_competitor
                with console.status(f"Instagram: {competitor.name}..."):
                    bundles = scrape_instagram_for_competitor(
                        competitor,
                        max_posts_per_profile=max_posts,
                        max_comments_per_post=max_comments,
                    )
            elif platform == "youtube":
                from strategy.youtube_comments import fetch_youtube_for_competitor
                with console.status(f"YouTube: {competitor.name}..."):
                    bundles = fetch_youtube_for_competitor(
                        competitor,
                        max_videos_per_channel=max_posts,
                        max_comments_per_video=max_comments,
                    )
        except EnvironmentError as e:
            status = f"auth error: {e}"
        except Exception as e:  # noqa: BLE001 — surface any scraper failure to the table
            status = f"{type(e).__name__}: {str(e)[:60]}"

        comment_count = 0
        for b in bundles:
            cache_bundle(client, b)
            comment_count += len(b.comments)
        total_comments += comment_count
        platforms_touched.add(platform)

        results_table.add_row(
            platform,
            competitor.name,
            str(len(bundles)),
            str(comment_count),
            status,
        )

    console.print(results_table)

    # ─── Refresh the voc dumps for every touched platform ─────────────────
    voc_paths: list[Path] = []
    for platform in sorted(platforms_touched):
        path = write_voc_dump(client, platform)
        if path is not None:
            voc_paths.append(path)

    if voc_paths:
        console.print("\n[green]VOC dumps written:[/green]")
        for p in voc_paths:
            console.print(f"  {p}")

    console.print(
        f"\n[bold]Total comments cached:[/bold] {total_comments}\n\n"
        f"[bold]Recommended next steps:[/bold]\n"
        f"  - [cyan]adc mine-voc --client {client} --category <category>[/cyan]  "
        f"(extract pains from new comments)\n"
        f"  - [cyan]adc analyze-gaps --client {client}[/cyan]  "
        f"(re-synthesize gap map with social signal)"
    )

    from strategy.cost_tracker import log_cost
    log_cost(
        client,
        "adc research-social",
        note=f"{len(plan)} (platform, competitor) pull(s), {total_comments} comment(s)",
    )


@cli.command(name="analyze-gaps")
@click.option("--client", required=True, help="Client slug")
@click.option("--synthesis-only", is_flag=True, default=False,
              help="Re-run only the cross-competitor synthesis using existing per-brand analyses")
def analyze_gaps(client: str, synthesis_only: bool):
    """Run competitive gap analysis on cached research. Produces competitive-gaps.md/.yaml."""
    from models.loader import load_brand
    from strategy.gap_analyzer import analyze_competitive_gaps

    brand = load_brand(client)
    if synthesis_only:
        console.print(f"[cyan]Re-synthesizing competitive gaps for {brand.name}...[/cyan]\n")
    else:
        console.print(f"[cyan]Analyzing competitive gaps for {brand.name}...[/cyan]")
        console.print("[dim]This runs ~5-6 Claude passes (~$1-2 total). Hold tight.[/dim]\n")

    output = analyze_competitive_gaps(client, brand.name, synthesis_only=synthesis_only)

    syn = output.get("synthesis", {})
    if syn.get("summary"):
        console.print(f"[green]TL;DR:[/green] {syn['summary']}\n")

    if syn.get("exploitable_gaps"):
        table = Table(title="Exploitable Gaps")
        table.add_column("Opportunity", style="cyan", max_width=40)
        table.add_column("Competitors failing", style="yellow", max_width=25)
        table.add_column("Ad angle", style="green", max_width=50)
        for g in syn["exploitable_gaps"]:
            table.add_row(
                str(g.get("opportunity", ""))[:60],
                ", ".join(g.get("competitors_failing", []))[:30],
                str(g.get("ad_angle", ""))[:80],
            )
        console.print(table)

    console.print(
        f"\n[green]Saved:[/green]\n"
        f"  clients/{client}/research/competitive-gaps.md\n"
        f"  clients/{client}/research/competitive-gaps.yaml"
    )

    from strategy.cost_tracker import log_cost
    cmd_name = "adc analyze-gaps-synthesis" if synthesis_only else "adc analyze-gaps"
    log_cost(client, cmd_name,
             note="synthesis only" if synthesis_only else "full per-brand + synthesis")


# ─── Exa Web Research ───────────────────────────────────────────────────────


@cli.command(name="research-web")
@click.option("--client", required=True, help="Client slug")
@click.option(
    "--competitors",
    default=None,
    help="Comma-separated competitor brand names (e.g. 'poppi,Health-Ade')",
)
@click.option(
    "--category",
    default=None,
    help="Comma-separated category terms for discussion queries "
    "(e.g. 'prebiotic soda,gut health drinks')",
)
@click.option(
    "--force-refresh",
    is_flag=True,
    default=False,
    help="Re-run queries even if cached. Default: skip cached (free re-runs).",
)
def research_web(client: str, competitors: str | None, category: str | None,
                 force_refresh: bool):
    """Run Exa web research for a client — Reddit + comparison + reviews + category."""
    from models.loader import load_brand
    from strategy.exa_research import run_research_bundle

    brand = load_brand(client)
    comp_list = [c.strip() for c in competitors.split(",")] if competitors else None
    cat_list = [c.strip() for c in category.split(",")] if category else None

    console.print(f"[cyan]Running Exa research for {brand.name}...[/cyan]")
    if comp_list:
        console.print(f"  Competitors: {', '.join(comp_list)}")
    if cat_list:
        console.print(f"  Category terms: {', '.join(cat_list)}")

    results = run_research_bundle(
        client_slug=client,
        brand_name=brand.name,
        competitors=comp_list,
        category_terms=cat_list,
        skip_cached=not force_refresh,
    )

    table = Table(title=f"Exa Research - {brand.name}")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Label", style="cyan")
    table.add_column("Category", style="yellow")
    table.add_column("Hits", style="green", justify="right")
    table.add_column("Top domain", style="dim")

    for i, r in enumerate(results, 1):
        top_domain = r.results[0].domain if r.results else "-"
        table.add_row(
            str(i),
            r.query.label,
            r.query.category,
            str(len(r.results)),
            top_domain,
        )

    console.print(table)
    out_dir = Path("clients") / client / "research" / "exa" / "raw"
    console.print(f"\n[green]Cached to: {out_dir}/[/green]")
    console.print(
        f"[dim]Total queries: {len(results)} | "
        f"Re-run free (cached) | --force-refresh to override[/dim]"
    )


