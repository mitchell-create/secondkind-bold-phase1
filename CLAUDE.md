# Claude Code Instructions

Read `AGENTS.md` first. It is the canonical operating guide for this migrated AdCreatives pipeline.

This repository started as `secondkind-bold-phase1`, but now carries the current AdCreatives pipeline snapshot for Claude Code use.

Key entry points:

- `docs/pipeline-rules.md`
- `docs/audience-conversion-report.md`
- `docs/phase-2-static-briefing-workflow.md`
- `cli.py`
- `strategy/audience_conversion.py`
- `strategy/phase2_static.py`

For new-client work, use `adc status --client <slug>` to check progress and follow the Phase 1 and Phase 2 gates in the docs.

Do not commit credentials, local `.env` files, generated ad output, browser state, or machine-local work directories.

Run paid research, scraping, image generation, remix, and bulk-generation commands only after explicit operator approval.
