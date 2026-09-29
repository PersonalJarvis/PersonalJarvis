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
  --good-soft:rgba(74,222,128,.12);--warn-soft:rgba(242,178,61,.14);--bad-soft:rgba(239,83,80,.14);
  --sunken:#1F1F1D;
  --s1:#C98500;--s2:#4F8EF7;--s3:#E0633F;--s4:#1F9E7F;--s5:#9085E9;--s6:#C84E8A;
  --seq-1:#5A4A12;--seq-2:#8A6E0C;--seq-3:#B9930A;--seq-4:#E0B30A;--seq-5:#FFD60A;
  --radius:8px;--radius-sm:4px;
  --font:"Inter",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:light){
:root:not([data-theme="dark"]){color-scheme:light;
  --bg:#FAF9F5;--bg-2:#FFFFFF;--bg-3:#F5F4ED;
  --ink:#141413;--ink-2:#6C6A5F;--ink-3:#8F8D82;
  --line:#E8E6DC;--line-2:#D9D6C9;
  --accent:#A86B00;--accent-ink:#FFFFFF;--accent-soft:rgba(168,107,0,.12);
  --good:#1F8A4C;--warn:#B26A00;--bad:#C0392B;
  --good-soft:rgba(31,138,76,.10);--warn-soft:rgba(178,106,0,.10);--bad-soft:rgba(192,57,43,.10);
  --sunken:#F0EEE6;
  --s1:#A86B00;--s2:#2A6FD0;--s3:#D4532E;--s4:#158F6B;--s5:#5B46C2;--s6:#C23F86;
  --seq-1:#F3E3A6;--seq-2:#E3C25A;--seq-3:#C99500;--seq-4:#A86B00;--seq-5:#7A4D00}}
:root[data-theme="light"]{color-scheme:light;
  --bg:#FAF9F5;--bg-2:#FFFFFF;--bg-3:#F5F4ED;
  --ink:#141413;--ink-2:#6C6A5F;--ink-3:#8F8D82;
  --line:#E8E6DC;--line-2:#D9D6C9;
  --accent:#A86B00;--accent-ink:#FFFFFF;--accent-soft:rgba(168,107,0,.12);
  --good:#1F8A4C;--warn:#B26A00;--bad:#C0392B;
  --good-soft:rgba(31,138,76,.10);--warn-soft:rgba(178,106,0,.10);--bad-soft:rgba(192,57,43,.10);
  --sunken:#F0EEE6;
  --s1:#A86B00;--s2:#2A6FD0;--s3:#D4532E;--s4:#158F6B;--s5:#5B46C2;--s6:#C23F86;
  --seq-1:#F3E3A6;--seq-2:#E3C25A;--seq-3:#C99500;--seq-4:#A86B00;--seq-5:#7A4D00}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 var(--font);
  -webkit-font-smoothing:antialiased;font-feature-settings:"cv02","cv03","cv04","cv11"}
h1,h2,h3{margin:0;line-height:1.2;letter-spacing:-.01em;text-wrap:balance}
h1{font-size:clamp(30px,4.6vw,50px);font-weight:700;line-height:1.05;letter-spacing:-.025em}
h2{font-size:21px;font-weight:650;letter-spacing:-.015em}
h3{font-size:16px;font-weight:600}
p{margin:0;max-width:68ch}
a{color:var(--accent);text-decoration:underline dotted;text-underline-offset:3px}
code,pre,.mono{font-family:var(--mono);font-size:.92em}
.eyebrow{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.14em;
  text-transform:uppercase;color:var(--accent)}
.card{background:var(--bg-2);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px}
.muted{color:var(--ink-2)}
.num{font-variant-numeric:tabular-nums}
/* --- The kit: the editorial report look, on brand. Use these classes; do not
   rebuild parallel ones. --- */
.wrap{max-width:1080px;margin:0 auto;padding:56px 32px 80px}
.prose{max-width:68ch}.prose>*+*{margin-top:14px}
.masthead{padding-bottom:28px;margin-bottom:8px;border-bottom:1px solid var(--line-2)}
.masthead h1{margin-top:14px}
.standfirst{margin-top:18px;font-size:18px;line-height:1.55;color:var(--ink-2);max-width:62ch}
.standfirst strong{color:var(--ink);font-weight:600}
.byline{margin-top:18px;font-family:var(--mono);font-size:11.5px;letter-spacing:.04em;color:var(--ink-3)}
.section{margin-top:56px}
.section-head{display:flex;align-items:baseline;justify-content:space-between;gap:16px;
  padding-bottom:10px;margin-bottom:20px;border-bottom:1px solid var(--line)}
.section-head .mark{font-family:var(--mono);font-size:11px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink-3);white-space:nowrap}
.split{display:grid;grid-template-columns:5fr 3fr;gap:40px;align-items:start}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;
  background:var(--line);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.metric{background:var(--bg-2);padding:18px 20px}
.metric .value{font-family:var(--mono);font-size:30px;font-weight:600;line-height:1.1;
  letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.metric .label{margin-top:6px;font-size:13px;color:var(--ink-2)}
.metric.is-key .value{color:var(--accent)}
.finding{display:grid;grid-template-columns:44px 1fr;gap:4px 16px;padding:18px 0 18px 16px;
  border-left:3px solid var(--line-2);border-bottom:1px solid var(--line)}
.finding .n{font-family:var(--mono);font-size:12px;color:var(--ink-3);padding-top:3px}
.finding h3{margin-bottom:6px}
.finding .src{margin-top:8px;font-family:var(--mono);font-size:11.5px;color:var(--ink-3)}
.finding.good{border-left-color:var(--good)}.finding.warn{border-left-color:var(--warn)}
.finding.bad{border-left-color:var(--bad)}.finding.key{border-left-color:var(--accent)}
.chip{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);font-size:10.5px;
  font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:2px 7px;
  border:1px solid currentColor;border-radius:var(--radius-sm);color:var(--ink-2)}
.chip.good{color:var(--good);background:var(--good-soft)}
.chip.warn{color:var(--warn);background:var(--warn-soft)}
.chip.bad{color:var(--bad);background:var(--bad-soft)}
.evidence{margin-top:10px;font-family:var(--mono);font-size:12.5px;line-height:1.55;
  background:var(--sunken);border-left:2px solid var(--line-2);padding:10px 14px;
  white-space:pre;overflow-x:auto;color:var(--ink-2)}
.verdict{display:grid;grid-template-columns:120px 1fr;gap:20px;align-items:baseline;
  padding:16px 0;border-bottom:1px solid var(--line)}
.verdict .tag{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3)}
.verdict.yes .tag{color:var(--accent)}
.callout{background:var(--bg-2);border:1px solid var(--line);border-left:3px solid var(--accent);
  border-radius:0 var(--radius) var(--radius) 0;padding:16px 20px}
.callout .eyebrow{display:block;margin-bottom:6px}
.table-wrap{overflow-x:auto}
.table{width:100%;border-collapse:collapse;font-size:14px}
.table th{font-family:var(--mono);font-size:11px;font-weight:600;letter-spacing:.08em;
  text-transform:uppercase;color:var(--ink-3);text-align:left;padding:8px 12px;
  border-bottom:1px solid var(--line-2)}
.table td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
.table .n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
.bar{display:grid;gap:6px;margin:14px 0}
.bar-head{display:flex;justify-content:space-between;gap:12px;font-size:14px}
.bar-head .v{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--ink-2)}
.bar-track{height:8px;background:var(--bg-3)}
.bar-fill{height:100%;width:var(--w);background:var(--ink-3);border-radius:0 4px 4px 0}
.bar-fill.is-key{background:var(--accent)}
.colophon{margin-top:72px;padding-top:16px;border-top:1px solid var(--line);display:flex;
  flex-wrap:wrap;justify-content:space-between;gap:12px;font-family:var(--mono);font-size:11px;
  letter-spacing:.05em;color:var(--ink-3)}
.colophon .brand{display:inline-flex;align-items:center;gap:8px;color:var(--ink-2)}
.gigi{width:18px;height:18px;flex:none}
.gigi .body{fill:#0E0E0E;stroke:var(--accent);stroke-width:10;stroke-linejoin:round}
.gigi .eye{fill:var(--accent)}
@media (max-width:760px){.wrap{padding:36px 18px 56px}.split{grid-template-columns:1fr;gap:24px}
.verdict{grid-template-columns:1fr;gap:6px}.finding{grid-template-columns:1fr}}
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

# The brand mark — the gigi ghost (jarvis/ui/web/frontend/src/components/
# MascotGigi.tsx, reduced to its silhouette and eyes) — for the colophon. It is
# the ONLY mark the page carries; the rejected gold star never appears.
GIGI_MARK_SVG: Final = (
    '<svg class="gigi" viewBox="0 0 256 256" aria-hidden="true">'
    '<path class="body" d="M58 90Q58 36 128 36Q198 36 198 90L198 208L180 186L160 208'
    'L140 186L120 208L100 186L80 208L58 186Z"/>'
    '<ellipse class="eye" cx="102" cy="108" rx="13" ry="17"/>'
    '<ellipse class="eye" cx="154" cy="108" rx="13" ry="17"/></svg>'
)

# The skeleton every page starts from. Its shape is what the strongest pages
# in the maintainer's own archive share (2026-09-29 review of twenty of them):
# eyebrow, a headline that is a claim, a standfirst that answers the question,
# then sections with a hairline head, then the colophon.
PAGE_SKELETON: Final = (
    '<main class="wrap">\n'
    '  <header class="masthead">\n'
    '    <p class="eyebrow">Diagnosis · Laptop performance</p>\n'
    "    <h1>The laptop crawls because one process holds a whole core</h1>\n"
    '    <p class="standfirst"><strong>Short answer: yes, it is the app.</strong> '
    "One render loop keeps 93 % of a core busy while the window is idle.</p>\n"
    "  </header>\n"
    '  <section class="section">\n'
    '    <div class="section-head"><h2>Where the time goes</h2>'
    '<span class="mark">§ 1 · measured</span></div>\n'
    "    …\n"
    "  </section>\n"
    '  <footer class="colophon">\n'
    '    <span class="brand">' + GIGI_MARK_SVG + "Personal Jarvis</span>\n"
    "    <span>Source: process sampling, 10-minute window</span>\n"
    "  </footer>\n"
    "</main>"
)


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

PAGE_ANATOMY: Final = """\
## Page anatomy — how a strong page is put together
Every page opens the same way, whatever its kind: an `.eyebrow` (the kind of page \
and its subject, e.g. "Diagnosis · Laptop performance"), an `<h1>` that states a \
CLAIM, and a `.standfirst` whose first sentence answers the user's question. Then \
the sections, each under a `.section-head`, and last the `.colophon`. Pick the \
archetype that fits the request and follow its order:
- Diagnosis / report / audit → the verdict in the standfirst; a `.metrics` strip or \
ONE hero number; numbered `.finding`s ranked by impact, each with the measured \
number, a status `.chip` (fixed / open / needs restart …) and an `.src` line naming \
where it comes from; an `.evidence` block where raw output proves the point; one \
recommendation in a `.callout`; "still open" last.
- Comparison / decision → `.verdict` rows ("Not so" / "It is so") that settle the \
misconception first; the options side by side with the same attributes in the same \
order (`.table`); ONE option marked Recommended in a `.callout` with the single \
reason it beats the runner-up; the rejected options stay listed, one line each.
- Explainer / pipeline → the whole picture first (one diagram of the stages), then \
the stages as a numbered sequence with input → output for each; what already exists \
vs. what is new.
- Brief / status → the one thing that needs the reader today at the top; then \
grouped lists (needs attention / on track / done) with times and owners in mono.
- Plan / checklist → the goal in one sentence, then steps as a real sequence with \
done/open state, then risks.
- Dashboard / data → the `.metrics` strip, then the chart(s), then a `.table` for the \
long tail.
- Tool / prototype / game → the working thing first, the reasoning (rules, what was \
rejected) calmly below it.
Use the kit (the classes in the token block: `.wrap .masthead .standfirst .byline \
.section .section-head .split .metrics .finding .chip .evidence .verdict .callout \
.table .bar .colophon`) instead of inventing parallel components; extend it only for \
what the kit does not cover, on the same tokens. The skeleton:
```html
""" + PAGE_SKELETON + """
```
The example text shows the register — replace every word with the request's own \
content. The colophon carries the gigi mark and "Personal Jarvis" on the left and, on \
the right, what the page is based on (the source, the time window, the assumption) — \
the only branding the page has; never a logo anywhere else."""

WRITING: Final = """\
## Writing — the copy is half the page
- Headlines are claims, not topics: "The wiki writes well and finds badly", not \
"Wiki analysis". Section headings are claims too wherever the section has a finding.
- The standfirst answers first ("Short answer: yes — …"), then gives the one reason. \
The reader who stops after it knows the result.
- Every finding carries its number with the unit (6 of 53, 93 %, 2.4 s) and its \
source; a claim without evidence is marked as an assumption.
- One recommendation, with the reason it beats the runner-up; trade-offs named in one \
plain clause ("but: the most expensive of the three").
- Short sentences, common words, no filler; labels as short as a person would say \
them — a stat label is two to four words, never a sentence.
- Length follows content: a page is as long as its findings, never padded; cut any \
section that restates another."""

DESIGN_SYSTEM: Final = """\
## Design system — the page is shown inside the Jarvis desktop app and must look like it
Paste the token block below VERBATIM as the first <style> in <head>, and the \
bootstrap script as the first <script> in <head>. The block is the tokens AND the \
component kit (see Page anatomy); your own CSS comes after it. Derive every colour, \
font and radius from these tokens; never introduce a hex that is not in the block (semantic status \
colours included). Dark is the default; light is fully defined; both are complete — \
scan your stylesheet before finishing: no colour may be defined ONLY inside a media \
or [data-theme] block.
- Accent (`--accent`) is spent in ONE place — the one number, the one highlighted \
series, the active state — and is quiet everywhere else. Never a gradient, never a \
glow, never a coloured headline.
- Surfaces: page `--bg`, cards `--bg-2`, recessed/hover `--bg-3`; 1px `--line` \
borders; radius `--radius` (8px) for the few cards there are, `--radius-sm` (4px) \
for chips and controls. Hairlines over cards: rows separated by 1px lines, a stat \
strip as a `gap:1px` grid on `--line` (`.metrics`), status as a 3px stripe at the \
start of a row (`.finding.bad`) — not a coloured box.
- Type: `--font` for reading text and headings; `--mono` for the typographic anchors — \
eyebrows, section marks, ids, file paths, stat values, table headers and columns of \
digits (tabular numbers). One type scale (h1 / h2 / h3 / body / 18px standfirst / 13px \
caption / 11px eyebrow) — stay on it, never an inline `style="font-size:…"`. Headings \
carry weight by size and weight, not by colour.
- Width: the page sits in `.wrap` (max 1080px); running text in a `.prose` column \
(~68 characters); asymmetric two-column layouts use `.split` (5fr / 3fr), never three \
equal columns by reflex.
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
- A stand-in for a logo: a lettered tile ("G", "OA", "A" in a coloured square), an emoji, \
an invented glyph or a generic "AI" spark beside a vendor's name — the original mark \
(supplied in the brief when the request names the brand) or the name in plain text, \
nothing in between.
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
- A headline that names a topic instead of stating a claim; a standfirst that \
restates the title; stat labels that are whole sentences; the same finding said twice.
- Hex colours or `#fff` written into component CSS (they break the other theme); \
radii of 12px and more; inline font sizes; a second logo or brand mark beside the \
colophon's.
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
- It opens with eyebrow, claim headline and answering standfirst, and closes with the \
colophon (gigi mark, "Personal Jarvis", the source).
- Nothing on the page says how it was built, which model wrote it, or what the \
instructions were.
- Nothing else was created or changed."""


__all__ = [
    "AVOID",
    "CHARTS",
    "DESIGN_SYSTEM",
    "DONE_MEANS",
    "GIGI_MARK_SVG",
    "PAGE_ANATOMY",
    "PAGE_SKELETON",
    "READ_THE_REQUEST",
    "THEME_BOOTSTRAP_JS",
    "THEME_CSS",
    "WRITING",
]
