# Image prompts for agent-receipt (text rendered by the model)

Use with a text-capable image model (GPT Image, Imagen 3/4, Ideogram, Flux). Paste one
prompt as-is. The text inside the `"..."` block must be reproduced exactly; keep the model's
"text accuracy" or "typography" mode on when it exists. Regenerate if any word is misspelled.

---

## Prompt 1 — README hero, 16:9 (recommended)

A soft pastel sky photographed on film at golden hour: medium blue sky (#6f9bd0 to #8db2dd)
with warm peach-and-apricot cumulus clouds, one large soft cloud mass along the lower left,
a few small wispy clouds in the upper right, fine film grain, matte and slightly hazy, calm
mood, no sun disc, no horizon, no land, no birds, no people.

Typography rendered ON the sky, all in pure white with a very soft dark shadow so it stays
readable over the clouds:

Line 1, upper left, large elegant serif typeface (like Iowan Old Style or Georgia), regular
weight, lowercase:
"agent-receipt"

Line 2, directly under it, smaller italic serif:
"a receipt for your Claude Code subagents"

Below, a block of text in a clean monospace terminal font, white, left-aligned, exactly these
seven lines with this indentation and these box-drawing characters:
"main session                 claude-fable-5-1   43 calls
├── Mine pain points          sonnet → sonnet-5  17 calls
└── Map tooling gaps          sonnet → sonnet-5  85 calls
    ├── Stickiness research   fork   sonnet-5    26 calls  !
    │     ! 5 spawn attempts failed: fork inside a fork
    ├── Stickiness research B fork   ran fable x19, sonnet x10  !
    └── Category survey       fork   ran sonnet x29, fable x7   !"

Last line, under the block, slightly larger monospace, white:
"37 findings · 41 failed spawn attempts"

Layout: the text occupies the left two thirds; keep the right third mostly open sky. Text
must be crisp, perfectly spelled, evenly spaced, no extra words, no watermark, no logos,
no UI window frame. Style: minimal, editorial, quiet. Aspect ratio 16:9.

---

## Prompt 2 — square social / avatar, 1:1

A soft pastel sky at golden hour on film: pale blue sky, peach-lit cumulus clouds hugging the
bottom edge, two small soft clouds near the top corners, an open calm blue area across the
middle. Film grain, low contrast, analog look, no sun, no horizon, no people.

Centered typography in pure white with a soft shadow:

Large serif typeface (Iowan Old Style / Georgia style), regular weight, lowercase, centered:
"agent-receipt"

Below it, a small monospace terminal block, centered as a group, left-aligned inside, exactly
these four lines:
"main session          claude-fable-5-1  43 calls
├── worker A          sonnet → sonnet-5  17 calls
└── worker B          fork   ran fable x19  !
      ! 5 spawn attempts failed"

Below that, one italic serif line, centered:
"who spawned whom · which model ran · what it cost"

All text perfectly spelled and legible, no extra characters, no watermark, no frame.
Aspect ratio 1:1.

---

## Prompt 3 — minimal poster, 16:9 or 4:5

The same pastel sky (blue with peach clouds, film grain, golden hour, no sun, no horizon).
Only two lines of white text, both centered:

Very large serif, lowercase:
"agent-receipt"

Under it, medium monospace, one line:
"37 findings · 3 agents ran the wrong model · 41 spawns failed"

Nothing else. Generous empty sky around the text. Perfectly spelled, crisp, no watermark.

---

## Tips

- If the tree characters (├ └ │) come out wrong, replace them in the prompt with two spaces
  and a plain hyphen: "  - Mine pain points ...". Most models handle that reliably.
- Ask for "white text with subtle drop shadow, high legibility" if letters blend into clouds.
- Reference for the look: the Anthropic "Fable 5.1" launch image (serif white title on a
  pastel cloud sky). Do not ask the model to copy it; describe the mood as above.
