# SecondKind Bold — Phase 1 Creative Strategy

A self-contained creative-strategy engine, pre-loaded with the full **SecondKind Bold** strategy. It turns brand research into personas, offers, a messaging strategy matrix, a brand voice with a bank of "unspoken truths," and ready-to-shoot creative briefs — and gives you a local dashboard to explore all of it.

Everything for SecondKind Bold is already generated and committed, so you can open the dashboard and read it immediately. The commands let you regenerate, extend, and tinker.

## What's inside

- **Research** — brand context, products, competitors, and a gap analysis
- **Personas** — 6 customer avatars, each with a psychology profile
- **Offers** — existing offers found on-site plus suggested new ones
- **Strategy matrix** — a persona × awareness-stage messaging map
- **Voice** — the brand's ownable voice and a persona-tagged bank of *unspoken truths* (each line is a ready-made hook)
- **Briefs** — creative briefs whose hooks pull from the voice bank
- **Dashboard** — a local web app to view all of the above

## Setup

Requires Python 3.11+.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 2. Install
pip install -e .

# 3. (Optional) Add an API key — only needed for the scripted `adc` generation commands
cp .env.example .env        # Windows: copy .env.example .env
# then edit .env and set ANTHROPIC_API_KEY
```

**You need no API key to view the strategy** — the dashboard just reads local files. A key is only needed to *generate new work* (briefs, voice, matrix), and you have two ways to do that, including a key-free one — see "Generating new work" below. The other keys in `.env.example` are only for re-running web research from scratch.

## View the dashboard

```bash
adc dashboard
```

Opens at http://localhost:8501. Pick **SecondKind Bold** and browse the tabs: Brand, Personas, Offers, Competitors, Gap Map, Psychology, **Voice**, Strategy, Briefs, and Matrix.

## Common commands

```bash
adc list-clients
adc status --client secondkind-bold                                  # what's done / what to run next
adc voice --client secondkind-bold                                   # (re)build the voice + unspoken-truths bank
adc brief --client secondkind-bold --product gut-balance --angles 6  # generate briefs (hooks pull from the voice bank)
adc strategy-matrix --client secondkind-bold                         # rebuild the messaging matrix
```

Run `adc --help` for the full list of commands.

## Generating new work (briefs, voice, hooks)

Two ways to create new creative against the SecondKind Bold strategy:

**A. Key-free, with Claude Code** (best for "just build me some briefs"). Open this folder in [Claude Code](https://claude.com/claude-code) and ask, e.g. *"build 6 new SecondKind Bold briefs for Gut Balance."* Claude reads the personas, the voice bank, and the gap map and writes the briefs directly — on your Claude subscription, **no API key and no per-run cost.** The `build-briefs` skill in `.claude/skills/` gives Claude the format and the rules. You can ask it for hooks, a refreshed voice, or scripts the same way.

**B. Scripted, with the `adc` CLI.** Set `ANTHROPIC_API_KEY` in `.env`, then:

```bash
adc brief --client secondkind-bold --product gut-balance --angles 6
```

Deterministic and runs the built-in validators; calls the Anthropic API (~$0.50 per 6-brief run on your account).

Either way, new briefs automatically pull hooks from the voice bank and exploit the competitive gap map shipped in this repo.

## Tinkering

The client data lives under `clients/secondkind-bold/`. Edit the YAML files (personas, offers, voice, etc.) and re-run the relevant command to regenerate downstream artifacts. The dashboard re-reads the files whenever you refresh.
