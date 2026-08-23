"""The design standard every artifact follows — the pattern, not the content.

Distilled on 2026-08-23 from two sources, in this order of authority:

1. the design guidance Claude itself follows when it builds an artifact
   (read the request first and calibrate the treatment; honour the existing
   design system; typography carries the page; pick neutrals, don't default;
   design both themes at token level; structure encodes information; a
   dashboard is scanned, a document is read; the catalogue of
   generated-looking defaults to avoid) and its data-visualisation method
   (form before colour, thin marks, one axis, legend for two or more series,
   labels selectively, colour follows the entity, validated palettes);
2. what our own archive showed when screenshotted the same day — the three
   pages a worker had produced were textbook generated design: a gradient
   headline over a centred hero, an eyebrow pill, emoji chips, seven to ten
   gradients, twenty-plus rounded corners, a Tailwind rainbow for categories,
   no light theme, and Google Fonts links the sandbox can never load.

So this module hands the worker three things the guidance says a page needs
and a model will not supply on its own: the **design system of the desktop
app it will be shown in** (the tokens below are the app's own light and dark
palettes, ``frontend/src/index.css``), a **way to read the request** so a
bar-chart ask yields a bar chart and a memo ask yields a memo, and the
**explicit list of what reads as generated**. The categorical series colours
were validated with the method's own checker on both surfaces (lightness
band, chroma floor, CVD separation, normal-vision floor, contrast) —
``#C98500,#4F8EF7,#E0633F,#1F9E7F,#9085E9,#C84E8A`` on ``#30302E`` and
``#A86B00,#2A6FD0,#D4532E,#158F6B,#5B46C2,#C23F86`` on white, all checks
passing; keep them in this order and re-validate before changing one.

Pure text; no I/O. ``brief.py`` assembles it into the mission prompt.
"""

from __future__ import annotations

from typing import Final

# --- The app's design system, as a token block the worker pastes verbatim -----
#
# Dark-first, because the desktop app is dark-first and the artifact is framed
# inside it. Light follows the OS preference OR an explicit stamp: the
# Artifacts stage passes the app's current theme as `?theme=light|dark`, and
# the bootstrap script below stamps `data-theme` from it — so the page follows
# the app, not the OS, whenever the app says which it is. Every colour is a
# token defined in BOTH palettes; nothing is defined only inside a media block
# (the classic unreadable-artifact bug).
THEME_CSS: Final = """\
:root{color-scheme:dark;
  --bg:#262624;--bg-2:#30302E;--bg-3:#363634;
  --ink:#F5F4EF;--ink-2:#9C9A92;--ink-3:#73716A;
  --line:#3B3A38;--line-2:#4A4946;
  --accent:#FFD60A;--accent-ink:#0A0A0A;--accent-soft:rgba(255,214,10,.14);
  --good:#4ADE80;--warn:#F2B23D;--bad:#EF5350;
  --s1:#C98500;--s2:#4F8EF7;--s3:#E0633F;--s4:#1F9E7F;--s5:#9085E9;--s6:#C84E8A;
  --seq-1:#5A4A12;--seq-2:#8A6E0C;--seq-3:#B9930A;--seq-4:#E0B30A;--seq-5:#FFD60A;
  --radius:12px;--radius-sm:8px;
  --font:"Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:light){
:root:not([data-theme="dark"]){color-scheme:light;
  --bg:#FAF9F5;--bg-2:#FFFFFF;--bg-3:#F5F4ED;
  --ink:#141413;--ink-2:#6C6A5F;--ink-3:#8F8D82;
  --line:#E8E6DC;--line-2:#D9D6C9;
  --accent:#A86B00;--accent-ink:#FFFFFF;--accent-soft:rgba(168,107,0,.12);
  --good:#1F8A4C;--warn:#B26A00;--bad:#C0392B;
  --s1:#A86B00;--s2:#2A6FD0;--s3:#D4532E;--s4:#158F6B;--s5:#5B46C2;--s6:#C23F86;
  --seq-1:#F3E3A6;--seq-2:#E3C25A;--seq-3:#C99500;--seq-4:#A86B00;--seq-5:#7A4D00}}
:root[data-theme="light"]{color-scheme:light;
  --bg:#FAF9F5;--bg-2:#FFFFFF;--bg-3:#F5F4ED;
  --ink:#141413;--ink-2:#6C6A5F;--ink-3:#8F8D82;
  --line:#E8E6DC;--line-2:#D9D6C9;
  --accent:#A86B00;--accent-ink:#FFFFFF;--accent-soft:rgba(168,107,0,.12);
  --good:#1F8A4C;--warn:#B26A00;--bad:#C0392B;
  --s1:#A86B00;--s2:#2A6FD0;--s3:#D4532E;--s4:#158F6B;--s5:#5B46C2;--s6:#C23F86;
  --seq-1:#F3E3A6;--seq-2:#E3C25A;--seq-3:#C99500;--seq-4:#A86B00;--seq-5:#7A4D00}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--font);
  -webkit-font-smoothing:antialiased;font-feature-settings:"cv02","cv03","cv04","cv11"}
h1,h2,h3{margin:0;line-height:1.2;letter-spacing:-.01em;text-wrap:balance}
h1{font-size:clamp(26px,3.2vw,34px);font-weight:650}
h2{font-size:20px;font-weight:600}
h3{font-size:16px;font-weight:600}
p{margin:0;max-width:68ch}
a{color:var(--accent)}
code,pre,.mono{font-family:var(--mono);font-size:.92em}
.eyebrow{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-2);font-weight:600}
.card{background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px}
.muted{color:var(--ink-2)}
.num{font-variant-numeric:tabular-nums}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion:reduce){
*,*::before,*::after{animation:none!important;transition:none!important}}"""

# Stamps `data-theme` from the `?theme=` query the Artifacts stage appends, so
# the page follows the app's theme. Harmless anywhere else (no query → OS).
THEME_BOOTSTRAP_JS: Final = """\
(function(){try{
var t=new URLSearchParams(location.search).get("theme");
if(t==="light"||t==="dark"){document.documentElement.setAttribute("data-theme",t);}
}catch(e){}})();"""


# --- The guide, section by section ---------------------------------------------

READ_THE_REQUEST: Final = """\
## Read the request first — the form follows the ask
The request names the deliverable; honour it literally before any rule below.
- Asked for a chart, "Balken", "Kurve", "Verlauf", "Anteile" → the chart IS the page: \
one clear chart (or a small set of them) with a one-line takeaway above it, not a \
landing page with a chart somewhere inside.
- Asked for a comparison, "Gegenüberstellung", "Vergleich" → a comparison table or \
side-by-side cards with the SAME attributes in the same order for every option.
- Asked to explain, "erklär mir", "wie funktioniert" → a readable document: short \
sections in reading order, running text at most ~68 characters wide, ONE diagram or \
chart exactly where it helps the explanation, never decoration.
- Asked for a dashboard, "Übersicht", "Status" → a UI that is scanned, not read: the \
summary (KPI row / hero figure) before the detail, charts below, a table for the \
long tail; state encoded in form (pill, chip, stripe) so what needs attention reads at \
a glance.
- Asked for a timeline, flow, hierarchy, mind map → a diagram drawn with CSS grid / \
flex or inline SVG: nodes as cards, hairline connectors, arrows only where direction \
carries meaning.
- Asked for a page, landing page, one-pager, tool, game → the editorial treatment: one \
real thesis at the top, then restraint.
Default when unsure: a calm, well-composed document. A utilitarian request (memo, \
plan, comparison, explanation) gets the same craft as a landing page, delivered \
quietly — no hero, no tagline, no marketing voice."""

DESIGN_SYSTEM: Final = """\
## Design system — the page is shown inside the Jarvis desktop app and must look like it
Paste the token block below VERBATIM as the first <style> in <head>, and the \
bootstrap script as the first <script> in <head>. Derive every colour, font and radius \
from these tokens; never introduce a hex that is not in the block (semantic status \
colours included). Dark is the default; light is fully defined; both are complete — \
scan your stylesheet before finishing: no colour may be defined ONLY inside a media \
or [data-theme] block.
- Accent (`--accent`) is spent in ONE place — the one number, the one highlighted \
series, the active state — and is quiet everywhere else. Never a gradient, never a \
glow, never a coloured headline.
- Surfaces: page `--bg`, cards `--bg-2`, recessed/hover `--bg-3`; 1px `--line` \
borders; radius `--radius` (12px) for cards, `--radius-sm` (8px) for controls — not \
every box is a rounded card, and a plain section with a hairline rule is often the \
better choice.
- Type: `--font` for everything, `--mono` only for code, ids, and columns of digits \
(`.num`). One type scale (h1 / h2 / h3 / body / 13px caption / 11px eyebrow) — stay on \
it. Headings carry weight by size and weight, not by colour.
- Spacing: siblings laid out with flex/grid + gap (never stacked margins); an 8px \
rhythm (8/12/16/24/32/48); wide tables, code and diagrams scroll inside their own \
`overflow-x:auto` container — the page body never scrolls sideways.
- Structure encodes information: an eyebrow, a number, a divider, a badge only when it \
says something true about the content. Numbered markers (01/02/03) ONLY for a real \
sequence. Status colour (`--good/--warn/--bad`) only when the colour MEANS good/bad, \
always with a label or icon, never colour alone.
- Copy is design material: the `<title>` is the page's name (a short noun phrase, no \
explainer after a dash), headings say what the reader gets, labels name things the way \
a person would, numbers carry their unit, captions state the source or assumption."""

CHARTS: Final = """\
## Charts and diagrams (when the request calls for them)
Draw charts with inline SVG (or canvas for dense data); write them against the tokens.
- Form before colour. A single current value → a stat tile (label, value, optional \
delta), not a one-bar chart. Magnitude compared → bars/columns. Trend → a line (area \
only for one series, fill at ~10% opacity). Part-to-whole → a stacked bar (horizontal \
for long names). Above/below a baseline → a diverging bar. Never a dual-axis chart: two \
measures of different scale get two charts side by side.
- Marks are thin and calm: bars ≤ 24px thick, 4px rounded at the data end and square at \
the baseline, a 2px surface gap between touching bars/segments; lines 2px with round \
joins; markers ≥ 8px with a 2px `--bg-2` ring; grid and axes hairline 1px solid in \
`--line`, never dashed; generous padding; the data is the only loud thing.
- Colour by job: ONE series → `--accent` (emphasis: the one series that matters in \
`--accent`, the rest in `--ink-3`). TWO OR MORE series → the categorical slots `--s1 … \
--s6` in that fixed order, never cycled, never generated; past six fold the tail into \
"Other". Magnitude → the sequential ramp `--seq-1 … --seq-5` (one hue, light→dark). \
Colour follows the entity, never its rank: filtering must not repaint survivors.
- Text wears text tokens: values, labels, axis ticks and legends in `--ink`/`--ink-2`, \
never in the series colour; identity comes from a swatch or line-key BESIDE the text.
- A legend is always present for two or more series (none for one); label selectively \
(the endpoint, the extreme, the series the story is about) — never a number on every \
point; axis ticks are round numbers with thousands separators.
- Every value is reachable without hovering: tooltips enhance, a compact table (or \
direct labels) carries the data too. Hit targets ≥ 24px.
- Diagrams: nodes are `.card`s (or plain labelled boxes), connectors hairline `--line-2` \
with small arrowheads only where direction matters, the current/selected node in \
`--accent`; a flow reads left→right or top→bottom, never both; no emoji icons — draw \
small inline-SVG icons or use none."""

AVOID: Final = """\
## What reads as generated — never ship these (they were in our own archive)
- A centred hero with a giant headline and a tagline on a page that is not a landing \
page; gradient text; glows, blurred colour blobs, radial backdrops; a gradient anywhere.
- Pills and chips as decoration (an "eyebrow pill" above the headline, emoji chips as \
filters); emoji as section markers or icons; 01/02/03 markers on non-sequences.
- The Tailwind rainbow (#3b82f6, #a855f7, #8b5cf6, #06b6d4, #10b981, #f59e0b, #f43f5e, \
slate greys) and any palette not in the token block; near-black with one acid-green \
or neon accent; purple-to-blue gradients.
- Every box a rounded card with an accent bar on its side; "rounded-lg everywhere"; \
cards nested in cards; three equal columns of icon + title + sentence.
- Marketing voice ("Next-Gen", "Intelligence Hub", "Tracker", "supercharge"); \
lorem ipsum; placeholder numbers; "TODO"; a title with an appended explainer \
("Dashboard — an interactive overview of…").
- Charts: dual axes, thick saturated blocks, dashed grids, rainbow categories, a value \
on every point, a 2-slice pie, legend-less multi-series, text in the series colour.
- Web fonts or anything fetched (the sandbox blocks every request — the page breaks \
silently); scattered micro-animations; animation that runs without being asked for."""

DONE_MEANS: Final = """\
## Done means
- The file opens from disk with no network and renders with no console errors; every \
script, style and image is inline.
- Both themes read correctly: open it once with `?theme=light` and once with \
`?theme=dark` (or flip the OS setting) — same hierarchy, legible contrast, accent \
working on both grounds; no colour defined only in a media/[data-theme] block.
- The page does exactly what the request asked — the chart the user named, the \
comparison, the explanation — with the real numbers and facts from the request; \
where something was missing, a clearly labelled assumption.
- Nothing on the page says how it was built, which model wrote it, or what the \
instructions were.
- Nothing else was created or changed."""


__all__ = [
    "AVOID",
    "CHARTS",
    "DESIGN_SYSTEM",
    "DONE_MEANS",
    "READ_THE_REQUEST",
    "THEME_BOOTSTRAP_JS",
    "THEME_CSS",
]
