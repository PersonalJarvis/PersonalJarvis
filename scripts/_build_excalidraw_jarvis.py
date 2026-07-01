"""One-shot builder for the Personal-Jarvis OpenClaw/ChatGPT visualisation.

NOT a templating generator — every element is spelled out explicitly with
its own text, coordinates and colour. The helpers only fill in the boring
constant fields (seed, index, version, the 20 boilerplate keys every
Excalidraw element needs) so the element list stays readable. This is the
pragmatic path at ~290 elements: coordinate math in Python beats
hand-editing raw JSON across 10 sections.

Run:  python scripts/_build_excalidraw_jarvis.py <out.excalidraw>
"""
from __future__ import annotations
import json, sys

ELEMENTS: list[dict] = []
_seed = [70000]

# ---- palette (semantic) ----------------------------------------------------
C = {
    "ink":   "#1e1e1e",   # primary text / strokes
    "sub":   "#495057",   # secondary text
    "det":   "#868e96",   # detail text
    "white": "#ffffff",
    # fills
    "green": "#b2f2bb", "green_s": "#2f9e44",
    "yellow":"#ffec99", "yellow_s":"#f08c00",
    "red":   "#ffc9c9", "red_s":  "#e03131",
    "blue":  "#a5d8ff", "blue_s": "#1971c2",
    "purple":"#d0bfff", "purple_s":"#9c36b5",
    "orange":"#ffd8a8", "orange_s":"#e8590c",
    "teal":  "#c3fae8", "teal_s": "#0ca678",
    "code_bg":"#1e1e1e", "code_tx":"#4dabf7", "code_tx2":"#a5d8ff",
    "code_gr":"#69db7c", "code_or":"#ffa94d",
}

def _next_seed() -> int:
    _seed[0] += 7
    return _seed[0]

def _base(eid: str, etype: str, x: float, y: float, w: float, h: float,
          stroke: str, bg: str, *, fill="solid", sw=2, ss="solid",
          rough=0, roundness=None) -> dict:
    return {
        "id": eid, "type": etype, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": stroke, "backgroundColor": bg,
        "fillStyle": fill, "strokeWidth": sw, "strokeStyle": ss,
        "roughness": rough, "opacity": 100, "groupIds": [], "frameId": None,
        "index": f"a{len(ELEMENTS):05d}", "roundness": roundness,
        "seed": _next_seed(), "version": 1, "versionNonce": _next_seed(),
        "isDeleted": False, "boundElements": [], "updated": 1779000000000,
        "link": None, "locked": False,
    }

def rect(eid, x, y, w, h, *, stroke=C["ink"], bg=C["white"], sw=2, ss="solid",
         round_=True):
    e = _base(eid, "rectangle", x, y, w, h, stroke, bg, sw=sw, ss=ss,
              roundness={"type": 3} if round_ else None)
    ELEMENTS.append(e); return e

def ellipse(eid, x, y, w, h, *, stroke=C["ink"], bg=C["white"], sw=2):
    e = _base(eid, "ellipse", x, y, w, h, stroke, bg, sw=sw)
    ELEMENTS.append(e); return e

def diamond(eid, x, y, w, h, *, stroke=C["ink"], bg=C["yellow"], sw=2):
    e = _base(eid, "diamond", x, y, w, h, stroke, bg, sw=sw)
    ELEMENTS.append(e); return e

def text(eid, x, y, s, *, size=16, color=C["ink"], w=None, h=None,
         align="left", font=7, bold_family=False):
    lines = s.split("\n")
    ww = w if w is not None else max(8, int(max(len(ln) for ln in lines) * size * 0.58))
    hh = h if h is not None else int(len(lines) * size * 1.25)
    e = _base(eid, "text", x, y, ww, hh, color, "transparent")
    e.update({
        "text": s, "originalText": s, "fontSize": size,
        "fontFamily": 5 if bold_family else font,
        "textAlign": align, "verticalAlign": "top",
        "containerId": None, "lineHeight": 1.25,
    })
    ELEMENTS.append(e); return e

def arrow(eid, x, y, dx, dy, *, stroke=C["ink"], sw=2, ss="solid",
          waypoints=None):
    pts = waypoints if waypoints else [[0, 0], [dx, dy]]
    e = _base(eid, "arrow", x, y, abs(dx) or 1, abs(dy) or 1, stroke,
              "transparent", sw=sw, ss=ss)
    e.update({"points": pts, "lastCommittedPoint": None,
              "startBinding": None, "endBinding": None,
              "startArrowhead": None, "endArrowhead": "arrow"})
    ELEMENTS.append(e); return e

def line(eid, x, y, pts, *, stroke=C["ink"], sw=2, ss="solid"):
    e = _base(eid, "line", x, y, 1, 1, stroke, "transparent", sw=sw, ss=ss)
    e.update({"points": pts, "lastCommittedPoint": None})
    ELEMENTS.append(e); return e

def code_box(eid_base, x, y, w, h, code_lines, *, title=None):
    """Dark evidence artifact with monospace-ish text."""
    rect(eid_base + "_bg", x, y, w, h, stroke="#343a40", bg=C["code_bg"], sw=1)
    yy = y + 10
    if title:
        text(eid_base + "_t", x + 12, yy, title, size=11, color=C["code_or"], font=3)
        yy += 20
    for i, (ln, col) in enumerate(code_lines):
        text(f"{eid_base}_l{i}", x + 12, yy, ln, size=11, color=col, font=3)
        yy += 17

# ===========================================================================
# SECTION 1 — HERO + SUMMARY FLOW
# ===========================================================================
# Hero band
rect("hero_bg", 0, 0, 4600, 250, stroke=C["ink"], bg="#f8f9fa", sw=3)
text("hero_title", 60, 50,
     "PERSONAL JARVIS — OpenClaw Mission-Engine & ChatGPT-Migration",
     size=58, color=C["ink"], bold_family=True)
text("hero_sub", 62, 135,
     "What was built: Voice → Force-Spawn → Mission-Manager → Worker/Critic-Loop → APPROVED → Voice-Readback   ·   plus the ChatGPT/Codex migration (Welle 6) and 18+ live bugs",
     size=20, color=C["sub"])
# info badges in hero
def badge(eid, x, y, label, fill, stroke):
    rect(eid + "_b", x, y, max(120, len(label) * 11 + 24), 40, stroke=stroke, bg=fill, sw=2)
    text(eid + "_t", x + 12, y + 11, label, size=15, color=C["ink"])
badge("hb1", 60, 190, "Stack: Phase-6 Mission-Manager", C["blue"], C["blue_s"])
badge("hb2", 520, 190, "Worker+Critic: codex exec (ChatGPT-OAuth)", C["green"], C["green_s"])
badge("hb3", 1160, 190, "STT: Groq Whisper (live) · Deepgram (research)", C["teal"], C["teal_s"])
badge("hb4", 1820, 190, "Brain: Gemini 3.5 Flash", C["purple"], C["purple_s"])
badge("hb5", 2280, 190, "Tests: 673+ green · /goal E2E proven", C["yellow"], C["yellow_s"])
badge("hb6", 2820, 190, "No Anthropic API · No login needed", C["orange"], C["orange_s"])

# Summary flow (the 10-second overview) — y ~ 300
sy = 320
text("sum_h", 60, sy - 36, "① SUMMARY FLOW — the whole path at a glance", size=22, color=C["ink"], bold_family=True)
sum_nodes = [
    ("sf_voice", "Voice /\nText", C["blue"], C["blue_s"], 60),
    ("sf_brain", "Brain-Router\n(force-spawn?)", C["purple"], C["purple_s"], 320),
    ("sf_tool", "spawn_openclaw\ntool", C["orange"], C["orange_s"], 620),
    ("sf_mgr", "Mission-\nManager", C["teal"], C["teal_s"], 920),
    ("sf_kontr", "Kontrollierer\n(orchestrator)", C["teal"], C["teal_s"], 1200),
    ("sf_worker", "CodexDirect\nWorker", C["green"], C["green_s"], 1500),
    ("sf_critic", "Critic-Loop\n(max 3)", C["yellow"], C["yellow_s"], 1800),
    ("sf_appr", "APPROVED", C["green"], C["green_s"], 2100),
    ("sf_voice2", "Voice-\nReadback", C["blue"], C["blue_s"], 2360),
]
for eid, label, fill, st, x in sum_nodes:
    rect(eid, x, sy, 230, 80, stroke=st, bg=fill, sw=2)
    text(eid + "_t", x + 16, sy + 22, label, size=16, color=C["ink"], align="left")
for i in range(len(sum_nodes) - 1):
    x0 = sum_nodes[i][4] + 230
    gap = sum_nodes[i+1][4] - x0
    arrow(f"sf_a{i}", x0 + 4, sy + 40, gap - 8, 0, stroke=C["sub"], sw=2)
# loop-back arrow critic -> worker
arrow("sf_loop", 1900, sy + 80, 0, 60, stroke=C["red_s"], sw=2,
      waypoints=[[0,0],[0,55],[-380,55],[-380,5]])
text("sf_loop_t", 1560, sy + 150, "revise → next iteration (≤3)", size=13, color=C["red_s"])

# ===========================================================================
# SECTION 2 — MAIN 15-STEP OpenClaw FLOW (hero, vertical lane A)
# ===========================================================================
mx = 60            # lane-A left
mw = 560           # step rect width
ex = 660           # evidence box left
ey0 = 560          # first step y
step_h = 110
gap = 95
text("flow_h", mx, ey0 - 40, "② DIE OpenClaw-LOGIK — Spawn bis APPROVED (file:line)",
     size=24, color=C["ink"], bold_family=True)

steps = [
    ("s1", "1 · Brain-Router force-spawn", C["purple"], C["purple_s"],
     "manager.py:1088 _should_force_openclaw",
     "Whisper-FP filter (EXACT_ONLY/PREFIX_OK) → smalltalk → action-verb → spawn"),
    ("s2", "2 · spawn_openclaw tool", C["orange"], C["orange_s"],
     "spawn_openclaw.py:218 execute()",
     "publishes OpenClawAnnouncement · fire-and-forget background dispatch"),
    ("s3", "3 · MissionManager.dispatch", C["teal"], C["teal_s"],
     "manager.py:84  → state PENDING",
     "persist-before-publish · MissionDispatched event · returns mission_id (UUID7)"),
    ("s4", "4 · Kontrollierer.run_mission", C["teal"], C["teal_s"],
     "orchestrator.py:201 (the heart)",
     "PENDING→RUNNING · TaskGroup + Semaphore(≤5) · one coroutine per Step"),
    ("s5", "5 · MissionDecomposer", C["blue"], C["blue_s"],
     "decomposer.py:77 decompose()",
     "<200 chars → 1-step plan, no LLM · Step.model default (sonnet→normalized)"),
    ("s6", "6 · WorktreeManager.create", C["blue"], C["blue_s"],
     "worktree.py:81 · ≤200-char cap",
     "git worktree add -b agent/<slug> · prune_and_sweep_leaked(6h) at boot"),
    ("s7", "7 · materialize_worker_contract", C["blue"], C["blue_s"],
     "workspace.py:46 → AGENTS.md",
     "writes contract into worktree + .git/info/exclude (BUG-021 fix)"),
    ("s8", "8 · build_worker_env", C["orange"], C["orange_s"],
     "env.py:50 · 6-var allowlist",
     "strips OPENAI_API_KEY+CODEX_HOME · OAuth-slot routing · no os.environ inherit"),
    ("s9", "9 · _worker_factory routing", C["purple"], C["purple_s"],
     "init.py:254 provider switch",
     "claude-api→ClaudeDirect · chatgpt→CodexDirect · else→SubJarvis"),
    ("s10", "10 · CodexDirectWorker.spawn", C["green"], C["green_s"],
     "codex_direct_worker.py · codex exec --json",
     "_normalize_model_for_codex · event-translation codex→Claude-shape"),
    ("s11", "11 · WorkerSupervisor", C["green"], C["green_s"],
     "supervisor.py classify state",
     "Done / Stuck / Waiting / TimedOut · feeds worker_error fast-fail"),
    ("s12", "12 · _capture_diff", C["yellow"], C["yellow_s"],
     "orchestrator.py:871",
     "git add -N + diff HEAD + ls-files --others · strips managed persona files"),
    ("s13", "13 · CriticRunner.run", C["yellow"], C["yellow_s"],
     "runner.py:346 · MAX_CRITIC_LOOPS=3",
     "_invoke_via_codex_direct · empty-diff pre-gate · CRITIC_UNAVAILABLE vs EXHAUSTED"),
    ("s14", "14 · _archive_task_artifacts", C["teal"], C["teal_s"],
     "orchestrator.py:945 (finally)",
     "copies best diff.patch before worktree teardown"),
    ("s15", "15 · MissionApproved → Voice", C["green"], C["green_s"],
     "voice/listener.py:118 readback",
     "scrub_for_voice (regex) → tts_speak_fn · only signed summary_de spoken"),
]
prev_y = None
for i, (eid, title, fill, st, ref, detail) in enumerate(steps):
    y = ey0 + i * (step_h + gap)
    rect(eid, mx, y, mw, step_h, stroke=st, bg=fill, sw=2)
    text(eid + "_t", mx + 16, y + 12, title, size=18, color=C["ink"], bold_family=True)
    text(eid + "_r", mx + 16, y + 44, ref, size=13, color=C["sub"], font=3)
    text(eid + "_d", mx + 16, y + 68, detail, size=12, color=C["det"])
    if prev_y is not None:
        arrow(f"flow_a{i}", mx + mw/2, prev_y + step_h, 0, gap,
              stroke=C["sub"], sw=2)
    prev_y = y

# Evidence artifacts beside the main flow
code_box("ev_force", ex, ey0 + 5, 560, 120, [
    ('_WHISPER_FP_EXACT_ONLY = {"you","musik",', C["code_tx2"]),
    ('   "applaus","subscribe","tschüss",...}', C["code_tx2"]),  # i18n-allow: reproduces the actual German STT false-positive word list
    ('# exact match only → "You there?" passes', C["code_gr"]),
    ('_WHISPER_FP_PREFIX_OK = {"vielen dank",', C["code_tx2"]),
    ('   "thanks for watching",...}  # startswith', C["code_tx2"]),
], title="Whisper-FP split (H2 fix)")

code_box("ev_env", ex, ey0 + 8*(step_h+gap) - 5, 560, 110, [
    ('if anthropic_api_key.startswith("sk-ant-oat"):', C["code_tx"]),
    ('    env["ANTHROPIC_OAUTH_TOKEN"] = key   # OAuth', C["code_gr"]),
    ('else:', C["code_tx"]),
    ('    env["ANTHROPIC_API_KEY"] = key       # classic', C["code_or"]),
    ('# oat01 in api-key slot → HTTP 400 (fixed)', C["code_gr"]),
], title="OAuth-slot routing (BUG-LIVE-FIX)")

code_box("ev_codex", ex, ey0 + 9*(step_h+gap) - 5, 560, 95, [
    ('cmd = ["codex","exec","--json",', C["code_tx2"]),
    ('  "--skip-git-repo-check","--sandbox",', C["code_tx2"]),
    ('  "workspace-write","-c","approval_policy=never",', C["code_tx2"]),
    ('  "--add-dir", str(worktree)]  # NO --model "sonnet"', C["code_gr"]),
], title="codex exec argv (Welle 6)")

code_box("ev_critic", ex, ey0 + 12*(step_h+gap) - 5, 560, 95, [
    ('# parse codex JSONL FIRST, exit-code SECOND', C["code_gr"]),
    ('for line: if item.type=="agent_message":', C["code_tx"]),
    ('    agent_texts.append(text)', C["code_tx2"]),
    ('# returncode=1 from MCP-crash tolerated', C["code_or"]),
], title="Critic parse-first (Welle 6)")

# ===========================================================================
# SECTION 3 — TOKEN / AUTH FLOWS (side-by-side comparison, lane B)
# ===========================================================================
tx = 1320
ty = 560
text("tok_h", tx, ty - 40, "③ TOKEN- & AUTH-FLOWS — pro Provider nebeneinander",
     size=24, color=C["ink"], bold_family=True)
cols = [
    ("tk_codex", "ChatGPT / Codex", C["green"], C["green_s"], tx, [
        ("Secret", "~/.codex/auth.json (OAuth file)"),
        ("Format", "access + refresh token pair"),
        ("Header", "Bearer <access> (internal)"),
        ("Jarvis", "STRIP OPENAI_API_KEY + CODEX_HOME"),
        ("Gotcha", "per-mission CODEX_HOME = no auth.json"),
        ("Status", "LIVE — worker + critic"),
    ]),
    ("tk_claude", "Anthropic / Claude", C["purple"], C["purple_s"], tx + 400, [
        ("Secret", "~/.claude/.credentials.json"),
        ("Format", "OAuth bearer sk-ant-oat01-..."),
        ("Slot", "ANTHROPIC_OAUTH_TOKEN (not API_KEY)"),
        ("Bug", "oat01 in API-KEY slot → 400"),
        ("Fix", "format-aware routing env.py:141"),
        ("Status", "fallback path"),
    ]),
    ("tk_groq", "Groq Whisper (STT)", C["teal"], C["teal_s"], tx + 800, [
        ("Secret", "keyring groq_api_key"),
        ("Tiers", "ctor → ENV GROQ_API_KEY → keyring"),
        ("Header", "Authorization: Bearer <key>"),
        ("Mode", "batch — single WAV POST"),
        ("Stream", "no (stream_transcribe = shim)"),
        ("Status", "LIVE — default STT"),
    ]),
    ("tk_dg", "Deepgram (research)", C["yellow"], C["yellow_s"], tx + 1200, [
        ("Secret", "DEEPGRAM_API_KEY (planned)"),
        ("Header", "Token <key>  (NOT Bearer!)"),
        ("Ephemeral", "POST /v1/auth/grant → JWT 30s-1h"),
        ("Stream", "interim_results=true ~150ms"),
        ("DE/EN", "Nova-3 yes · Flux EN-only"),
        ("Status", "GHOST — no source files"),
    ]),
]
for eid, title, fill, st, x, rows in cols:
    rect(eid, x, ty, 380, 320, stroke=st, bg=C["white"], sw=2)
    rect(eid + "_hd", x, ty, 380, 46, stroke=st, bg=fill, sw=2, round_=False)
    text(eid + "_ht", x + 16, ty + 13, title, size=18, color=C["ink"], bold_family=True)
    ry = ty + 60
    for j, (k, v) in enumerate(rows):
        text(f"{eid}_k{j}", x + 16, ry, k, size=12, color=st, bold_family=True)
        text(f"{eid}_v{j}", x + 110, ry, v, size=12, color=C["sub"])
        ry += 42

# ===========================================================================
# SECTION 4 — THE 3-CHAINED ChatGPT-MIGRATION BUGS (Welle 6)
# ===========================================================================
cy = 960
text("chain_h", tx, cy - 38, "④ WELLE 6 — the 3 chained live bugs of the ChatGPT migration",
     size=24, color=C["ink"], bold_family=True)
chain = [
    ("ch1", "C1 · CODEX_HOME leak", "per-mission CODEX_HOME ohne auth.json",
     "→ 'Error finding codex home'",
     "FIX: strip CODEX_HOME aus env", tx),
    ("ch2", "C2 · MCP-Plugin-Crash", "Cloudflare-MCP OAuth expired beim Bootstrap",
     "→ frisst agent_message frame",
     "FIX: --ignore-user-config (critic)", tx + 540),
    ("ch3", "C3 · sonnet-Model-Reject", "Decomposer model='sonnet' → codex 400",
     "'sonnet not supported on ChatGPT'",
     "FIX: _normalize_model_for_codex → ''", tx + 1080),
]
for eid, title, rc, sym, fix, x in chain:
    rect(eid, x, cy, 500, 200, stroke=C["red_s"], bg="#fff5f5", sw=2)
    rect(eid + "_hd", x, cy, 500, 44, stroke=C["red_s"], bg=C["red"], sw=2, round_=False)
    text(eid + "_t", x + 16, cy + 12, title, size=17, color=C["ink"], bold_family=True)
    text(eid + "_rc", x + 16, cy + 58, "Ursache:", size=12, color=C["red_s"], bold_family=True)
    text(eid + "_rcv", x + 16, cy + 78, rc, size=13, color=C["sub"], w=470)
    text(eid + "_sym", x + 16, cy + 112, sym, size=12, color=C["det"], font=3, w=470)
    text(eid + "_fx", x + 16, cy + 150, fix, size=13, color=C["green_s"], bold_family=True, w=470)
arrow("ch_a1", tx + 500, cy + 100, 40, 0, stroke=C["red_s"], sw=3)
arrow("ch_a2", tx + 1040, cy + 100, 40, 0, stroke=C["red_s"], sw=3)
text("ch_proof", tx, cy + 220,
     "Live proof: mission_019e3c51 + 019e3c52 stream.jsonl → HTTP 400 'sonnet not supported'  ·  after fix: /goal E2E green in 48s",
     size=13, color=C["sub"])

# ===========================================================================
# SECTION 5 — BUG-REGISTER TIMELINE (lane C, vertical)
# ===========================================================================
bx = 3060
by0 = 560
text("bug_h", bx, by0 - 40, "⑤ BUG-REGISTER — die Chronik (18+)",
     size=24, color=C["ink"], bold_family=True)
line("bug_spine", bx + 14, by0, [[0, 0], [0, 3150]], stroke=C["det"], sw=3)
bugs = [
    ("BUG-LIVE-01", "git add -N misses files on Windows", C["yellow"]),
    ("BUG-LIVE-02", "Critic approves empty diff from text claim", C["red"]),
    ("BUG-LIVE-03", "Session-ID reuse forces wrong model", C["yellow"]),
    ("BUG-LIVE-04", "Whisper-FP triggers phantom missions", C["red"]),
    ("BUG-LIVE-05", "Windows worktree remove fails under lock", C["yellow"]),
    ("CRIT-1", "Critic→Anthropic REST → 100% HTTP 400", C["red"]),
    ("CRIT-2", "Worker never gets AGENTS.md contract", C["red"]),
    ("CRIT-3", "ANTHROPIC_API_KEY blocks OAuth path", C["red"]),
    ("CRIT-4", "Wiki schema.md missing → empty recall", C["orange"]),
    ("CRIT-5", "Hang-up leaves voice in THINKING", C["orange"]),
    ("BUG-LIVE-08", "claude_md_inject false-positive blocks", C["yellow"]),
    ("BUG-LIVE-09", "empty-diff gate rejects Read-Only", C["yellow"]),
    ("BUG-024", "Critic agent registration race (3 ep)", C["red"]),
    ("BUG-LIVE-10", "UUID7 8-char prefix collision → 13", C["yellow"]),
    ("Welle 6 C1", "CODEX_HOME breaks ChatGPT OAuth", C["green"]),
    ("Welle 6 C2", "MCP plugin crash swallows critic", C["green"]),
    ("Welle 6 C3", "'sonnet' rejected by ChatGPT backend", C["green"]),
    ("BUG-028", "Capability hallucination (3-layer)", C["purple"]),
    ("Deepgram", "Ghost registrations + batch-only pipeline", C["blue"]),
]
bstep = 3150 / len(bugs)
for i, (bid, desc, fill) in enumerate(bugs):
    y = by0 + i * bstep
    ellipse(f"bug_d{i}", bx + 6, y - 8, 18, 18, stroke=C["ink"],
            bg=C["green_s"] if "Welle 6" in bid else C["red_s"])
    rect(f"bug_b{i}", bx + 44, y - 16, 200, 30, stroke=C["ink"], bg=fill, sw=1)
    text(f"bug_id{i}", bx + 52, y - 10, bid, size=12, color=C["ink"], bold_family=True)
    text(f"bug_de{i}", bx + 260, y - 9, desc, size=12, color=C["sub"], w=560)

# ===========================================================================
# SECTION 6 — STATE MACHINE
# ===========================================================================
smx = 1320
smy = 1320
text("sm_h", smx, smy - 38, "⑥ MISSION STATE-MACHINE", size=22, color=C["ink"], bold_family=True)
sm_nodes = {
    "PENDING":   (smx + 0,   smy + 40, C["blue"], C["blue_s"]),
    "RUNNING":   (smx + 240, smy + 40, C["yellow"], C["yellow_s"]),
    "CRITIQUING":(smx + 490, smy + 40, C["orange"], C["orange_s"]),
    "LOOPING":   (smx + 490, smy + 160, C["orange"], C["orange_s"]),
    "APPROVED":  (smx + 760, smy + 40, C["green"], C["green_s"]),
    "FAILED":    (smx + 760, smy + 160, C["red"], C["red_s"]),
}
for name, (x, y, fill, st) in sm_nodes.items():
    rect(f"sm_{name}", x, y, 200, 70, stroke=st, bg=fill, sw=2)
    text(f"sm_{name}_t", x + 14, y + 24, name, size=16, color=C["ink"], bold_family=True)
arrow("sm_a1", smx + 200, smy + 75, 40, 0, stroke=C["sub"])
arrow("sm_a2", smx + 440, smy + 75, 50, 0, stroke=C["sub"])
arrow("sm_a3", smx + 690, smy + 75, 70, 0, stroke=C["green_s"])
arrow("sm_a4", smx + 590, smy + 110, 0, 50, stroke=C["sub"])  # critiquing->looping
arrow("sm_a5", smx + 490, smy + 195, -250, -120, stroke=C["sub"], ss="dashed",
      waypoints=[[0,0],[-180,0],[-180,-120],[-250,-120]])  # looping->running
arrow("sm_a6", smx + 590, smy + 110, 170, 50, stroke=C["red_s"])  # critiquing->failed
text("sm_loop", smx + 250, smy + 200, "≤ 3 critic loops", size=12, color=C["sub"])
text("sm_term", smx, smy + 260,
     "Terminal: APPROVED · FAILED · CANCELLED · TIMED_OUT  ·  illegal edge → IllegalStateTransition (never silent)",
     size=12, color=C["det"])

# ===========================================================================
# SECTION 7 — EVENT TYPES (MissionBus)
# ===========================================================================
evx = 1320
evy = 1700
text("evt_h", evx, evy - 36, "⑦ MISSION-BUS EVENT-TYPEN", size=22, color=C["ink"], bold_family=True)
events = [
    ("MissionDispatched", "prompt, language, priority", C["blue"]),
    ("MissionPlanReady", "plan[], n_workers", C["blue"]),
    ("WorkerSpawned", "worker_id, pid, cli, model", C["teal"]),
    ("WorkerProgress", "pct, tokens, cost", C["teal"]),
    ("WorkerDraftReady", "artifact_uri, diff", C["teal"]),
    ("CriticVerdictReady", "verdict, confidence, axes", C["yellow"]),
    ("WorkerCorrectionRequired", "correction, next_model", C["orange"]),
    ("WorkerKilled", "reason (timeout/budget/...)", C["red"]),
    ("MissionApproved", "summary_de, tokens, cost", C["green"]),
    ("MissionFailed", "reason, error_class", C["red"]),
    ("MissionStateChanged", "from, to, reason", C["purple"]),
    ("MissionBudgetWarning", "pct_used, limit_usd", C["orange"]),
]
for i, (name, fields, fill) in enumerate(events):
    y = evy + i * 44
    rect(f"evt_{i}", evx, y, 320, 36, stroke=C["ink"], bg=fill, sw=1)
    text(f"evt_n{i}", evx + 12, y + 9, name, size=13, color=C["ink"], bold_family=True)
    text(f"evt_f{i}", evx + 340, y + 9, fields, size=12, color=C["sub"], font=3)

# ===========================================================================
# SECTION 8 — ISOLATION INVARIANTS
# ===========================================================================
isx = 1820
isy = 1760
text("iso_h", isx, isy - 36, "⑧ ISOLATION-INVARIANTEN", size=22, color=C["ink"], bold_family=True)
inv = [
    "1 git worktree per task (≤200-char path)",
    "2 Windows Job Object — KILL_ON_JOB_CLOSE",
    "3 MAX_CRITIC_LOOPS = 3 (hardcoded, ADR)",
    "4 no session reuse across critic iters",
    "5 persist-before-publish (event log = truth)",
    "6 env allowlist — 6 vars, no os.environ",
    "7 worktree cleanup in finally + 6h sweep",
]
for i, t in enumerate(inv):
    y = isy + i * 50
    rect(f"iso_{i}", isx, y, 600, 40, stroke=C["teal_s"], bg=C["teal"], sw=1)
    text(f"iso_t{i}", isx + 14, y + 11, t, size=14, color=C["ink"])

# ===========================================================================
# SECTION 9 — DEEPGRAM RESEARCH FINDINGS
# ===========================================================================
dgx = 1320
dgy = 2680
rect("dg_panel", dgx, dgy, 1080, 360, stroke=C["blue_s"], bg="#f1f8ff", sw=2)
text("dg_h", dgx + 20, dgy + 16, "⑨ DEEPGRAM RESEARCH (2026-05-20) — research only, no code",
     size=22, color=C["ink"], bold_family=True)
text("dg_q1", dgx + 20, dgy + 62, "Question 1 — Auth different from Groq?", size=15, color=C["blue_s"], bold_family=True)
text("dg_a1", dgx + 20, dgy + 88,
     "YES: 'Token <key>' instead of 'Bearer'  +  optional short-lived JWTs (POST /v1/auth/grant, 30s-1h).\nServer-side it's a 1-line difference, though. Ephemeral JWT flow is only needed for browser clients.",
     size=13, color=C["sub"])
text("dg_q2", dgx + 20, dgy + 150, "Question 2 — tokens while you're speaking?", size=15, color=C["blue_s"], bold_family=True)
text("dg_a2", dgx + 20, dgy + 176,
     "YES: interim_results=true → first tokens ~150ms WHILE speaking. is_final/speech_final flags.\nFlux model: EagerEndOfTurn → the LLM starts before you're done. Nova-3 DE+EN, Flux EN-only.",
     size=13, color=C["sub"])
text("dg_q3", dgx + 20, dgy + 238, "Surprise — repo reality:", size=15, color=C["red_s"], bold_family=True)
text("dg_a3", dgx + 20, dgy + 264,
     "Deepgram does NOT exist — 3 entry points in pyproject.toml point to non-existent files\n('ghost registrations'). The pipeline is batch-only: _handle_utterance → transcribe_pcm(whole sentence).\nReal live listen-along needs a plugin build AND a pipeline rework.",
     size=13, color=C["sub"])
badge("dg_st", dgx + 20, dgy + 320 - 4, "Decision: from-scratch build, not finish-the-stub", C["yellow"], C["yellow_s"])

# ===========================================================================
# SECTION 10 — /goal E2E PROOF (lane A bottom)
# ===========================================================================
gx = 60
gy = 3700
rect("goal_panel", gx, gy, 1160, 300, stroke=C["green_s"], bg="#ebfbee", sw=2)
text("goal_h", gx + 20, gy + 16, "⑩ /goal LIVE E2E PROOF — the whole stack green",
     size=22, color=C["ink"], bold_family=True)
text("goal_f", gx + 20, gy + 56, "tests/integration/test_voice_mission_e2e_chatgpt.py", size=14, color=C["sub"], font=3)
gchecks = [
    "bootstrap_missions → real stack spun up",
    "manager.dispatch + kontrollierer.run_mission",
    "CodexDirectWorker spawns a real codex exec (no mock)",
    "Critic via _invoke_via_codex_direct (read-only)",
    "Mission-DB state == APPROVED",
    "Proof file with exact content (from diff.patch)",
    "CriticVerdictReady(verdict='approve') bus event",
]
for i, t in enumerate(gchecks):
    y = gy + 92 + i * 28
    ellipse(f"goal_c{i}", gx + 24, y + 2, 14, 14, stroke=C["green_s"], bg=C["green"])
    text(f"goal_ct{i}", gx + 48, y, t, size=13, color=C["sub"])
badge("goal_res", gx + 620, gy + 100, "1 passed in 48s · codex login status EXIT 0", C["green"], C["green_s"])
badge("goal_res2", gx + 620, gy + 160, "skips clean if not logged in (CI-safe)", C["blue"], C["blue_s"])
badge("goal_res3", gx + 620, gy + 220, "no Anthropic key · no OpenAI key · pure OAuth", C["orange"], C["orange_s"])

# ---- cross-section connector: summary → main flow ----
arrow("x_sum_main", 175, 400, -60, 130, stroke=C["det"], sw=1, ss="dashed",
      waypoints=[[0,0],[0,90],[-40,90],[-40,130]])


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "out.excalidraw"
    doc = {
        "type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
        "elements": ELEMENTS,
        "appState": {"gridSize": 20, "gridStep": 5, "gridModeEnabled": False,
                     "viewBackgroundColor": "#ffffff"},
        "files": {},
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"wrote {out} — {len(ELEMENTS)} elements")
