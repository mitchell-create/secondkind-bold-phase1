# SecondKind Bold — Phase 1 Creative Strategy (notes for Claude)

This repo holds the complete Phase 1 creative strategy for **SecondKind Bold**, a postbiotic gut-health brand (bold/challenger voice variant). You can generate new creative — briefs, video ad ideas, hooks, scripts, and anything riffed off the strategy matrix — **directly from the data here, no API key required**: read the files and write the output. There's also a scripted CLI (`adc`) that does the same via the Anthropic API (see README), but you don't need it to do the thinking yourself.

## Where the strategy lives (`clients/secondkind-bold/`)

- `brand.yaml`, `brand-context.md`, `positioning.md` — who the brand is, tone, the bold-voice spine
- `avatars/*.yaml` — 6 personas, each with a `psychology_profile` (dominant/weak heuristics, emotional quadrant, recommended/avoid creative pairings)
- `products/gut-balance.yaml` — benefits, unique mechanism, objections, social proof, price
- `offers.yaml` — existing + suggested offers
- `strategy-matrix.yaml` / `.md` — persona × awareness messaging map
- `research/competitive-gaps.yaml` / `.md` — the exploitable competitor gaps (strongest hook fodder)
- `voc/extracted_pains.yaml` — voice-of-customer corpus (pains, money quotes, language)
- `voice.yaml` — the brand voice + the per-persona **unspoken-truths bank** (each line is a ready hook)
- `briefs/*.yaml` — existing briefs (match this schema and style)

## To generate creative — briefs, video ad ideas, hooks, scripts

Use the **build-creative** skill at `.claude/skills/build-creative/SKILL.md`. It covers all of it: creative briefs, video ad ideas/concepts (including from a Matrix pain point), hook lists, scripts, and captions.

**The rule that applies to EVERYTHING you generate:** every customer-facing opening line comes from the voice bank first. Open `clients/secondkind-bold/voice.yaml`, find the target persona's `unspoken_truths`, and start from one of those lines (or write a new one in the same confessional register), then deliver the mechanism and the receipt. Whether the ask is "build briefs," "video ad ideas based on a Matrix pain point," or "10 hooks for the perimenopause persona," you open from the bank — never with a generic claim. Then honor the persona's psychology profile, exploit a real gap from `research/competitive-gaps.yaml`, and obey the hard rules below.

## Hard rules (always)

- **Never name a competitor brand** in customer-facing copy — abstract to the category or the mechanism.
- **No scarcity or fake urgency** (a weak lever for this whole cohort).
- **Lead with what the product does**, not company/operational claims (guarantee, shipping, subscription close — they don't open).
- **Hooks come from the voice bank first**; set `hook_source: voice-bank` when you use one.
- **Sentence case**; no hype words (miracle, revolutionary, game-changer, transform your, journey, ritual, unlock your, …).
- The cut lands on the **category or the mechanism, never the customer**. Body/appearance language is off-limits for Natalie, Paul, and Isaac — see each persona's `register_note` in `voice.yaml`.
