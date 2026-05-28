---
name: build-creative
description: Generate any SecondKind Bold creative from this repo's strategy data — creative briefs, video ad ideas/concepts, hook lists, scripts, captions, and ideas riffed off a Matrix pain point. Always opens from the voice/unspoken-truths bank and obeys the brand rules. Key-free (no Anthropic API needed); read the files and write the output. Use whenever asked to "build briefs", "video ad ideas", "ad concepts", "give me hooks", "write scripts", or generate any creative for SecondKind Bold — including from the strategy matrix.
---

# SecondKind Bold — creative generation

You generate creative for SecondKind Bold by reading the strategy in this repo and writing it out — no API key, no `adc` CLI required. This covers **briefs, video ad ideas/concepts, hook lists, scripts, captions, and anything riffed off a Matrix pain point.**

## The one rule that applies to EVERYTHING you generate

**Every customer-facing opening line comes from the voice bank first.** Open `clients/secondkind-bold/voice.yaml`, find the target persona's `unspoken_truths`, and start from one of those lines (or write a new line in the exact same register). Then follow the voice's `structural_move`: open in the customer's confessional register, then deliver the mechanism and the receipt. This holds for briefs, video hooks, static hooks, scripts, and captions alike — a SecondKind ad never opens with a generic claim; it opens with an unspoken truth.

(Nuance: a pure stat or contrast hook may not map to a bank line. In that case, write in the same blunt, vindicating register and note where the line came from.)

## Inputs (all under `clients/secondkind-bold/`)

- `voice.yaml` — **the most important input.** The brand voice + each persona's `unspoken_truths` bank, plus `voice_register`, `structural_move`, `guardrail_test`, and per-persona `register_note` (off-limits topics).
- `avatars/<persona>.yaml` — the persona, including its `psychology_profile` (dominant heuristics to lean on, weak ones + `avoid_pairings` to avoid).
- `products/gut-balance.yaml` — benefits, `unique_mechanism`, objections, social proof, price.
- `strategy-matrix.yaml` / `.md` — the Matrix: persona × awareness cells, each with a pain and an angle. This is what "a pain point in our Matrix" refers to.
- `research/competitive-gaps.yaml` — exploitable competitor gaps (strong hook fodder; each has evidence + an ad angle).
- `voc/extracted_pains.yaml` — verbatim customer language and money quotes.
- `briefs/*.yaml` — existing briefs (the format to match for briefs).

## Hard rules (non-negotiable — also in `voice.yaml` → `hard_rules_retained`)

- **Never name a competitor brand** in customer-facing copy. Abstract to the category or mechanism.
- **No scarcity / urgency / countdowns.**
- **Lead with the product/mechanism, not company or operational claims** (guarantee, subscription, shipping close — they don't open).
- **Sentence case.** No hype words: miracle, revolutionary, game-changer, transform your, journey, ritual, unlock your, etc.
- **FDA structure-function:** bold language in the hook/body; "helps support" / "may help" qualifiers in fine print only.
- **The cut lands on the category or the mechanism, never the customer.** Body/appearance language is off-limits for **Natalie, Paul, and Isaac** — check each persona's `register_note`.

---

## A. Briefs (the structured deliverable)

Write each brief to `clients/secondkind-bold/briefs/<brief_id>.yaml`, matching the existing files:

```yaml
brief_id: secondkind-bold-gut-balance-<random 6 hex>
client: secondkind-bold
product: Gut Balance
awareness_level: problem_aware        # unaware | problem_aware | solution_aware | product_aware | most_aware
mental_stage: trigger                 # trigger | exploration | evaluation | purchase
trigger_moment: ""                    # fill ONLY for trigger stage
framework: pas                        # pas | aida | bab | fab | four_cs | quest | pastor | slap
angle: "one-line messaging angle"
hook: "the actual scroll-stopping opening line (from the voice bank)"
hook_type: "Surprising Stat"          # from the diversity matrix below
slot: 1
hook_source: "voice-bank"             # 'voice-bank' when from the bank; else e.g. "Pain: <verbatim quote>"
hook_tactic: "the specific tactic"
persona: "Done-Everything Danielle"
persona_traits: "one-line buyer thumbnail"
creative_mechanic: "e.g. Pattern Interrupt with Reveal"
visual_format: "e.g. Editorial typographic static, or Founder talking-head (video)"
visual_format_alternatives: ["alt 1", "alt 2"]
pain_point: "the pain this targets"
benefit_callouts: ["short", "short", "short"]
cta: "call to action"
visual_direction: "what the visual should convey"
target_platform: meta
source_insight: "claude-native"
```

Method: pick a persona + awareness stage → pick a diversity slot (`1 Surprising Stat · 2 Story/Result · 4 Curiosity Gap · 5 Direct Address · 6 Contrast/Enemy · 7 Question · 8 Pattern Interrupt · 9 Controversial · 10 Problem-Solution`; **skip slot 3 FOMO/Urgency** and any slot in the persona's `weak_heuristics`) → take the hook from the persona's `unspoken_truths` (`hook_source: voice-bank`) → apply the psychology profile → exploit a gap in at least half the set → write to the assigned mental stage → vary framework, mechanic, and format across the set.

## B. Video ad ideas / concepts (including from a Matrix pain point)

When asked for video ad ideas — often "based on a brief" or "a pain point in the Matrix":

1. **Find the source.** A cell in `strategy-matrix.yaml` (each persona × awareness cell has a pain + angle), an existing brief, or a pain the user names.
2. **Identify the persona**, and pull the `unspoken_truth` from `voice.yaml` that matches that pain.
3. **Build the concept on the four-beat arc:** open on the unspoken truth (the confessional hook) → diagnose the mechanism → vindicate ("you weren't wrong, the product was") → close on the offer.
4. **Give each idea:** the hook line (from the bank), a 1–2 line concept/treatment, the format (e.g. founder talking-head, text-over-B-roll, UGC confessional, voiceover-over-product), and which persona + Matrix pain it serves.
5. **Honor the persona register** (no body language for Natalie/Paul/Isaac) and all hard rules.

## C. Hooks, scripts, captions

Same principle: pull from the persona's `unspoken_truths`, stay in the register, obey the rules. For a hook list, draw several truths for that persona and vary the tactic. For a script, expand one truth across the four-beat arc.

## Output

- **Briefs** → write files under `clients/secondkind-bold/briefs/`.
- **Video ideas / hooks / scripts** → present in the chat (or save to a file if asked), grouped by persona, each tied to its source pain and the bank line it opened from.

Then summarize what you produced — persona, source pain, and opening line for each — so the user can spot-check that it's on-voice.
