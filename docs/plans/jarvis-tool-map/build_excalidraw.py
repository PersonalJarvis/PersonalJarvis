"""Deterministic Excalidraw poster generator for the Personal Jarvis architecture.

Builds one large `.excalidraw` JSON file that maps the tool surface Jarvis can
actually call, the voice->response flow, the mission/critic loop, memory tiers,
the new-feature subsystems (with honest live/partial/open status), and the
speech/audio I/O — derived from a 5-agent file:line-verified survey.

Layout philosophy (overlap-free by construction):
  * The canvas is a vertical stack of large "rooms" (regions).
  * Inside a room, cards live on a fixed-column grid with gutters, so two cards
    can never overlap. Card height is derived from wrapped text, so text can
    never overflow.
  * Arrows stay inside a region (chains / loops) or run as a few thick labelled
    connectors along clear vertical channels between stacked regions.
  * The tool catalog is shown as a grouped grid (membership = grouping, not a
    40-arrow fan-out hairball).

Run:  python build_excalidraw.py
Out:  <USER_HOME>/OneDrive/Ex/2026-05-24/jarvis-tool-architecture.excalidraw
"""
from __future__ import annotations

import json
import os

# --------------------------------------------------------------------------
# id / seed counters (deterministic)
# --------------------------------------------------------------------------
_C = {"n": 0}


def _nid(prefix: str = "e") -> str:
    _C["n"] += 1
    return f"{prefix}{_C['n']:05d}"


def _seed() -> int:
    _C["n"] += 1
    return 100000 + _C["n"] * 7

UPDATED = 1779900000000

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------
INK = "#1e1e1e"
SUB = "#495057"
WHITE = "#ffffff"

FILL = {
    "live": "#d3f9d8", "partial": "#fff3bf", "open": "#ffe3e3",
    "info": "#e7f5ff", "brain": "#f3f0ff", "mem": "#e6fcf5",
    "flow": "#d0ebff", "evt": "#f1f3f5", "ui": "#fff9db",
    "neutral": "#f8f9fa",
}
STROKE = {
    "live": "#2f9e44", "partial": "#f08c00", "open": "#e03131",
    "info": "#1971c2", "brain": "#7048e8", "mem": "#0ca678",
    "flow": "#1c7ed6", "evt": "#868e96", "ui": "#f59f00",
    "neutral": "#adb5bd",
}

# region theme colours
T_FLOW = "#1c7ed6"
T_BRAIN = "#7048e8"
T_TOOL = "#1971c2"
T_MISSION = "#e8590c"
T_MEM = "#0ca678"
T_FEAT = "#9c36b5"
T_SPEECH = "#1098ad"
T_AUDIO = "#0b7285"
T_EVT = "#495057"
T_UI = "#f59f00"

FONT_BODY = 7
FONT_HEAD = 5
LINE_H = 1.25
CHARW = 0.60  # width estimate factor (over-estimate -> text always fits)

PAD = 12
ANCHORS: dict[str, dict] = {}
REGION_BOUNDS: dict[str, tuple] = {}
ELEMENTS: list[dict] = []


# --------------------------------------------------------------------------
# low-level element factories
# --------------------------------------------------------------------------
def rect(x, y, w, h, *, fill=WHITE, stroke=INK, sw=1.5, style="solid",
         rough=0, round_type=3, bg_dashed=False):
    e = {
        "id": _nid("r"), "type": "rectangle",
        "x": round(x, 2), "y": round(y, 2),
        "width": round(w, 2), "height": round(h, 2),
        "angle": 0, "strokeColor": stroke, "backgroundColor": fill,
        "fillStyle": "solid", "strokeWidth": sw,
        "strokeStyle": "dashed" if bg_dashed else style,
        "roughness": rough, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": ({"type": round_type} if round_type else None),
        "seed": _seed(), "version": 1, "versionNonce": _seed(),
        "isDeleted": False, "boundElements": [], "updated": UPDATED,
        "link": None, "locked": False,
    }
    ELEMENTS.append(e)
    return e


def text(x, y, s, *, fs=12, color=INK, font=FONT_BODY, align="left", w=None, h=None):
    lines = s.split("\n")
    if w is None:
        w = max((len(ln) for ln in lines), default=1) * fs * CHARW + 4
    if h is None:
        h = len(lines) * fs * LINE_H + 4
    e = {
        "id": _nid("t"), "type": "text",
        "x": round(x, 2), "y": round(y, 2),
        "width": round(w, 2), "height": round(h, 2),
        "angle": 0, "strokeColor": color, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": _seed(), "version": 1, "versionNonce": _seed(),
        "isDeleted": False, "boundElements": [], "updated": UPDATED,
        "link": None, "locked": False, "text": s, "fontSize": fs,
        "fontFamily": font, "textAlign": align, "verticalAlign": "top",
        "containerId": None, "originalText": s, "autoResize": True,
        "lineHeight": LINE_H,
    }
    ELEMENTS.append(e)
    return e


def arrow(x1, y1, x2, y2, *, label=None, color=INK, sw=2.5, dashed=False,
          waypoints=None):
    if waypoints is None:
        pts = [[0, 0], [x2 - x1, y2 - y1]]
    else:
        allp = [(x1, y1)] + waypoints + [(x2, y2)]
        pts = [[round(px - x1, 2), round(py - y1, 2)] for px, py in allp]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    e = {
        "id": _nid("a"), "type": "arrow",
        "x": round(x1, 2), "y": round(y1, 2),
        "width": round(max(xs) - min(xs), 2), "height": round(max(ys) - min(ys), 2),
        "angle": 0, "strokeColor": color, "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": sw,
        "strokeStyle": "dashed" if dashed else "solid",
        "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": {"type": 2}, "seed": _seed(), "version": 1,
        "versionNonce": _seed(), "isDeleted": False, "boundElements": [],
        "updated": UPDATED, "link": None, "locked": False,
        "points": pts, "lastCommittedPoint": None,
        "startBinding": None, "endBinding": None,
        "startArrowhead": None, "endArrowhead": "arrow",
    }
    ELEMENTS.append(e)
    if label:
        midx = (x1 + x2) / 2
        midy = (y1 + y2) / 2
        text(midx + 6, midy - 16, label, fs=10, color=color)
    return e


# --------------------------------------------------------------------------
# text wrapping + card
# --------------------------------------------------------------------------
def wrap(s, max_w, fs):
    max_chars = max(8, int((max_w - 2 * PAD) / (CHARW * fs)))
    out = []
    for para in s.split("\n"):
        words = para.split()
        cur = ""
        if not words:
            out.append("")
            continue
        for wd in words:
            if len(cur) + len(wd) + 1 <= max_chars:
                cur = (cur + " " + wd).strip()
            else:
                if cur:
                    out.append(cur)
                while len(wd) > max_chars:
                    out.append(wd[:max_chars])
                    wd = wd[max_chars:]
                cur = wd
        if cur:
            out.append(cur)
    return out


def card_height(title, desc, w):
    tl = wrap(title, w, 13)
    dl = wrap(desc, w, 10) if desc else []
    h = PAD + len(tl) * 13 * LINE_H
    if dl:
        h += 4 + len(dl) * 10 * LINE_H
    return max(56, h + PAD), tl, dl


def card(x, y, w, item, *, anchor=None):
    """item = (title, desc, status). Returns (height)."""
    title, desc, status = item
    h, tl, dl = card_height(title, desc, w)
    rect(x, y, w, h, fill=FILL.get(status, FILL["neutral"]),
         stroke=STROKE.get(status, INK), sw=2, round_type=3)
    # status accent bar (left edge) — colored badge
    rect(x, y, 6, h, fill=STROKE.get(status, INK), stroke=STROKE.get(status, INK),
         sw=0.5, round_type=0)
    ty = y + PAD * 0.6
    text(x + PAD + 4, ty, "\n".join(tl), fs=13, color=INK)
    if dl:
        ty2 = ty + len(tl) * 13 * LINE_H + 4
        text(x + PAD + 4, ty2, "\n".join(dl), fs=10, color=SUB)
    if status in ("open", "partial"):
        pill_w = 46 if status == "open" else 58
        rect(x + w - pill_w - 6, y + 6, pill_w, 16,
             fill=STROKE[status], stroke=STROKE[status], sw=0.5, round_type=3)
        text(x + w - pill_w - 1, y + 7, "OPEN" if status == "open" else "PARTIAL",
             fs=9, color=WHITE)
    if anchor:
        ANCHORS[anchor] = {"x": x, "y": y, "w": w, "h": h,
                           "cx": x + w / 2, "cy": y + h / 2}
    return h


def grid(cards, x0, y0, avail_w, ncols, gx=22, gy=20):
    """Place cards on a grid that FILLS avail_w. Card width is derived so the
    columns span the full available width (no empty right gutter). Returns
    bottom_y."""
    cw = (avail_w - (ncols - 1) * gx) / ncols
    col = 0
    rowy = y0
    rowh = 0
    for it in cards:
        anchor = None
        if len(it) == 4:
            it, anchor = it[:3], it[3]
        if col == ncols:
            rowy += rowh + gy
            rowh = 0
            col = 0
        cx = x0 + col * (cw + gx)
        h = card(cx, rowy, cw, it, anchor=anchor)
        rowh = max(rowh, h)
        col += 1
    return rowy + rowh


def room(title, subtitle, theme, x, y, w, content_fn, key=None):
    """Draw a region room sized to its content. content_fn(cx, cy)->bottom_y."""
    head = 64
    cx = x + 26
    cy = y + head
    bottom = content_fn(cx, cy, w - 52)
    h = bottom - y + 30
    # room rect first (so it sits behind) -> insert at recorded position
    rrect = rect(x, y, w, h, fill=FILL["neutral"], stroke=theme, sw=2.5,
                 round_type=3)
    # move room rect to just before its content
    ELEMENTS.pop()
    ELEMENTS.insert(_room_insert_at[0], rrect)
    # title band
    text(x + 26, y + 16, title, fs=24, color=theme, font=FONT_HEAD)
    if subtitle:
        text(x + 26, y + 44, subtitle, fs=12, color=SUB)
    if key:
        REGION_BOUNDS[key] = (x + w / 2, y, y + h)
    return y + h


# room insertion bookkeeping
_room_insert_at = [0]


def begin_room():
    _room_insert_at[0] = len(ELEMENTS)


# --------------------------------------------------------------------------
# CONTENT (curated from the 5-agent file:line survey)
# --------------------------------------------------------------------------
MARGIN = 140
PAGE_W = 3680
Y = 60
GAP = 90


def hrule_title():
    global Y
    rect(MARGIN, Y, PAGE_W, 150, fill="#0e0d0c", stroke="#e7c46e", sw=3, round_type=3)
    text(MARGIN + 40, Y + 26, "PERSONAL JARVIS — TOOL-CALLING & ARCHITECTURE MAP",
         fs=40, color="#e7c46e", font=FONT_HEAD)
    text(MARGIN + 42, Y + 84,
         "Voice -> Router-Brain -> Tool dispatch -> Worker/Critic -> Memory -> Voice.   "
         "Honest status from a file:line-verified 5-agent survey (2026-05-24).",
         fs=15, color="#f1f3f5")
    text(MARGIN + 42, Y + 112,
         "GREEN = live   ·   AMBER = partial / dormant   ·   RED = open / dead wiring.   "
         "Built for: 'does Jarvis actually CALL these tools?'",
         fs=13, color="#adb5bd")
    Y += 150 + 30


def legend():
    global Y
    bx = MARGIN
    rect(bx, Y, PAGE_W, 96, fill=WHITE, stroke=T_EVT, sw=2, round_type=3)
    text(bx + 20, Y + 12, "LEGEND", fs=16, color=T_EVT, font=FONT_HEAD)
    items = [
        ("live", "live in code"), ("partial", "partial / dormant"),
        ("open", "open / dead"), ("flow", "voice flow step"),
        ("brain", "brain / routing"), ("mem", "memory tier"),
        ("info", "tool / dispatch"), ("evt", "event / bus"),
    ]
    x = bx + 20
    for st, lab in items:
        rect(x, Y + 44, 26, 26, fill=FILL[st], stroke=STROKE[st], sw=2, round_type=2)
        text(x + 34, Y + 49, lab, fs=12, color=INK)
        x += 250
    # edge-kind legend
    text(bx + 20, Y + 74, "Arrows:  control ->   data -->   spawn =>   event ~>   (kinds are labelled).",
         fs=11, color=SUB)
    Y += 96 + GAP


def region_ui():
    global Y
    begin_room()
    views = ["Chats", "Agents", "Skills", "Plugins", "Docs", "MCPs", "Tasks",
             "Review", "Transcription", "Terminal", "Board", "Languages",
             "Profile", "Wiki", "API Keys", "Orb overlay", "Tray app"]
    cards = [(v, "", "ui") for v in views]

    def content(cx, cy, w):
        return grid(cards, cx, cy, w, 8, gx=18, gy=14)

    Y = room("L7 · UI / UX surface", "FastAPI + React + pywebview desktop app; sidebar views, Orb overlay, tray",
             T_UI, MARGIN, Y, PAGE_W, content) + GAP


def region_input():
    global Y
    begin_room()
    chain = [
        ("MicrophoneCapture", "WASAPI in · 16kHz mono int16 (audio/capture.py)", "live", "in.mic"),
        ("_wake_loop fanout", "one mic -> 2 detector queues (pipeline.py:1642)", "live", "in.fanout"),
        ("openWakeWord 'hey_jarvis'", "ONNX · thresh 0.10 BUG-009 (wake/openwakeword_provider.py:47)", "live", "in.oww"),
        ("RollingWhisperWake", "robust parallel wake fallback (rolling_whisper_wake.py)", "live"),
        ("Silero VAD", "512-sample frames, silence_ms=1000 (audio/vad.py:28)", "live", "in.vad"),
        ("STT stability probe", "anti speaker-bleed force-endpoint (pipeline.py:767)", "live"),
        ("TurnBuffer", "merges multi-fragment turns + auto-flush (turn_buffer.py)", "live"),
        ("_handle_utterance", "STT->guards->brain->scrub->speak (pipeline.py:1943)", "flow", "in.handle"),
        ("FasterWhisper distil-large-v3", "default local utterance STT, CUDA (stt/fwhisper.py:21)", "live", "in.fw"),
        ("Groq Whisper-v3 (cloud)", "cloud STT fallback ~200-400ms (stt/groq_api.py)", "live"),
        ("Guard: wake-only", "bare wake turn -> skip brain (pipeline.py:1997)", "live"),
        ("Guard: STT hallucination", "drops YouTube/outro hallucinations (pipeline.py:2014)", "live"),
        ("Guard: 'auflegen' hangup", "hard kill: player.stop()+gate (pipeline.py:1571)", "partial"),
    ]

    def content(cx, cy, w):
        return grid(chain, cx, cy, w, 4, gx=24, gy=18)

    Y = room("L2 · Speech INPUT chain", "wake -> VAD -> STT, with the pre-brain guards",
             T_SPEECH, MARGIN, Y, PAGE_W, content, key="input") + GAP
    # local arrows: mic->fanout->oww/vad->handle->fw
    # intra-region flow is conveyed by reading order + the region-gap spine;
    # no long diagonal connectors across the grid.


def region_core():
    global Y
    begin_room()
    core = [
        ("BrainManager.generate()", "intent-router + fallback + force-spawn (manager.py:1519)", "brain", "core.bm"),
        ("_should_force_openclaw", "smalltalk>verb>marker, strict default (manager.py:1088)", "brain", "core.fs"),
        ("local_action_fast_path", "deterministic local tool, NO LLM (manager.py:1179)", "brain", "core.local"),
        ("ROUTER tier (single)", "sub-jarvis deleted Welle4; only 'router' (factory.py:119)", "brain", "core.router"),
        ("ROUTER_TOOLS frozenset", "13 + 3 self-mod = 16 live tools (factory.py:40)", "info", "core.rt"),
        ("ToolExecutor.execute", "ONLY authorized exec path (safety/tool_executor.py:105)", "info", "core.te"),
        ("RiskTierEvaluator", "blacklist>whitelist>default (safety/risk_tier.py:100)", "info", "core.risk"),
        ("Plugin Registry", "entry_points discovery jarvis.tool (core/registry.py:47)", "info", "core.reg"),
        ("Provider fallback chain", "dead_providers + RateLimitTracker (manager.py:1674)", "brain"),
        ("Runtime provider switch", "voice 'switch to Gemini' (manager.py:841)", "brain"),
        ("Supervisor / TurnTaking FSM", "IDLE/LISTENING/THINKING/SPEAKING (supervisor.py:26)", "brain"),
        ("MissionManager (bootstrap)", "lazy-resolver wiring (missions/init.py:59)", "partial"),
    ]

    def content(cx, cy, w):
        return grid(core, cx, cy, w, 4, gx=24, gy=18)

    Y = room("L6/L4/L3 · Orchestrator core — the decision hub",
             "BrainManager picks: local action -> force-spawn worker -> or LLM tool-use via ROUTER_TOOLS",
             T_BRAIN, MARGIN, Y, PAGE_W, content, key="core") + GAP
    # (no diagonal intra-region connector; ToolExecutor->catalog shown by the
    # region-gap spine below)


def region_flow():
    global Y
    begin_room()
    steps = [
        ("1 · Wake / Hotkey", "WakeWordDetected -> mic opens", "flow"),
        ("2 · STT finalize", "transcribe_pcm -> TranscriptFinal", "flow"),
        ("3 · Pre-brain guards", "wake-only / hallucination / hangup / privacy", "flow"),
        ("4 · Flash-Brain ACK", "parallel sub-sec preamble (suppress-if-fast)", "flow"),
        ("5 · Meta-cmd gate", "cancel / switch / depth / status (pattern-first)", "flow"),
        ("6 · local_action_gate", "deterministic local tool, no LLM", "flow"),
        ("7 · force-spawn OR LLM router", "heavy worker  OR  ROUTER_TOOLS tool-use loop", "flow"),
        ("8 · Tool dispatch", "ToolExecutor.execute -> ActionExecuted", "flow"),
        ("8b · Worker spawn (heavy)", "spawn_openclaw -> Mission/Kontrollierer", "flow"),
        ("9 · Result aggregate", "ResponseGenerated / BackgroundCompleted", "flow"),
        ("10 · scrub_for_voice", "regex-only, NO LLM (AP-11)", "flow"),
        ("11 · TTS -> audio", "_speak -> synthesize -> WASAPI out", "flow"),
    ]

    def content(cx, cy, w):
        ncols = 6
        cardw = (w - (ncols - 1) * 22) / ncols
        rows = [steps[:6], steps[6:]]
        yy = cy
        for r, row in enumerate(rows):
            maxh = 0
            for c, s in enumerate(row):
                x = cx + c * (cardw + 22)
                h = card(x, yy, cardw, s, anchor=f"flow.{r}.{c}")
                maxh = max(maxh, h)
            ym = yy + maxh / 2
            for c in range(len(row) - 1):
                x1 = cx + c * (cardw + 22) + cardw
                x2 = cx + (c + 1) * (cardw + 22)
                arrow(x1, ym, x2, ym, color=T_FLOW, sw=2.5)
            yy += maxh + 46
        return yy

    Y = room("THE VOICE -> RESPONSE FLOW (ordered)",
             "manager.generate() canonical sequence — row 1 = steps 1-6, row 2 = steps 7-12. This is where Jarvis decides to call a tool.",
             T_FLOW, MARGIN, Y, PAGE_W, content, key="flow") + GAP


def region_catalog():
    global Y
    begin_room()
    clusters = [
        ("LIVE ROUTER TOOLS (16) — the only tools the voice brain can call", [
            ("run-shell", "monitor · shell exec (plugins/tool/run_shell.py:22)", "live"),
            ("screen-snapshot", "monitor · screen capture (screen_snapshot.py:92)", "live"),
            ("dispatch-to-harness", "monitor · route to harness (dispatch_to_harness.py)", "live"),
            ("multi-spawn", "monitor · parallel spawns (multi_spawn.py)", "live"),
            ("spawn-openclaw", "monitor · spawn Mission (spawn_openclaw.py:124)", "live", "cat.spawn"),
            ("dispatch-with-review", "monitor · quality-gate pipeline (dispatch_with_review.py)", "live"),
            ("awareness-snapshot", "safe · sync state read (awareness_snapshot.py:42)", "live"),
            ("awareness-recall", "safe · FTS5/BM25 episodes (awareness_recall.py:82)", "live"),
            ("run-skill", "monitor · run user skill (run_skill.py:42)", "live"),
            ("wiki-recall", "safe · vault keyword search (wiki_recall.py:54)", "live"),
            ("wiki-page-read", "safe · read by vault path (wiki_page_read.py)", "live"),
            ("wiki-ingest", "monitor · save fact (wiki_ingest.py:75)", "live"),
            ("list_mutable_settings", "safe · list config keys (self_mod_tools.py:94)", "live"),
            ("get_config_value", "safe · read config (self_mod_tools.py:153)", "live"),
            ("set_config_value", "ask · atomic config write (self_mod_tools.py:253)", "live"),
        ]),
        ("LOCAL-ACTION FAST-PATH (resolved in-process, no LLM)", [
            ("open_app", "monitor · local-action gate (open_app.py:95)", "live"),
            ("type_text", "safe · local-action gate (type_text.py:81)", "live"),
            ("hotkey", "monitor · local-action gate (hotkey.py:141)", "live"),
            ("reset_orb_position", "safe · orb recovery (reset_orb_position.py:32)", "live"),
        ]),
        ("COMPUTER-USE ONLY (loaded into CU context, NOT router)", [
            ("click", "monitor · CU loop only (click.py:109)", "partial"),
            ("move-mouse", "safe · CU loop only (move_mouse.py)", "partial"),
            ("switch-window", "monitor · CU loop only (switch_window.py)", "partial"),
            ("read-visible-ui-state", "safe · CU verify (read_visible_ui_state.py)", "partial"),
            ("wait-for-ui-state", "safe · CU wait (wait_for_ui_state.py)", "partial"),
        ]),
        ("REGISTERED BUT NOT IN ROUTER_TOOLS (legacy / partial)", [
            ("dispatch-to-admin", "ask · UAC-helper IPC, not router (dispatch_to_admin.py)", "partial"),
            ("search-web", "safe · legacy brain only (search_web.py)", "partial"),
            ("remember", "safe · superseded by wiki-ingest (remember.py)", "partial"),
            ("whoami", "safe · legacy only (whoami.py)", "partial"),
            ("verify-via-curl", "safe · self-verify, not router (verify_via_curl.py)", "partial"),
            ("verify-localhost", "safe · self-verify, not router (verify_localhost.py)", "partial"),
            ("start-preview-server", "safe · not router (start_preview_server.py)", "partial"),
            ("cli-tools", "block · virtual loader, legacy brain only (clis/loader.py:21)", "partial"),
        ]),
        ("DEAD / OPEN WIRING (the honesty story)", [
            ("spawn-cli-worker", "entry-point points to MISSING file (pyproject.toml:184)", "open"),
            ("spawn_skill_author", "registered but NOT in ROUTER_TOOLS (skill_authoring.py:27)", "open"),
        ]),
        ("MCP / MARKETPLACE TOOL SOURCE (decoupled from router!)", [
            ("MCPRegistry", "loads mcp.json, starts servers (mcp/registry.py:56)", "live"),
            ("MCPToolAdapter", "wraps MCP tool as Tool '<server>/<name>' (mcp/adapter.py:54)", "live"),
            ("app.state.tool_registry", "holds MCP adapters (desktop_app.py:827)", "partial"),
            ("brain_dispatcher == None", "NEVER set -> MCP tools never reach router (desktop_app.py:862)", "open", "cat.mcpbreak"),
            ("Marketplace catalog", "installs MCP server, no direct brain tools (catalog.py)", "partial"),
        ]),
    ]

    def content(cx, cy, w):
        yy = cy
        for ctitle, cards in clusters:
            text(cx, yy, ctitle, fs=15, color=T_TOOL, font=FONT_HEAD)
            yy += 28
            yy = grid(cards, cx, yy, w, 5, gx=20, gy=16)
            yy += 30
        return yy

    Y = room("THE TOOL CATALOG — every tool, grouped by reachability",
             "Membership shown by grouping (no 40-arrow fan-out). ToolExecutor is the only legal call path.",
             T_TOOL, MARGIN, Y, PAGE_W, content, key="catalog") + GAP


def region_mission():
    global Y
    begin_room()
    chain = [
        ("spawn_openclaw tool", "router tool, dispatch+run (spawn_openclaw.py:116)", "live", "m.entry"),
        ("MissionManager.dispatch()", "upsert PENDING + MissionDispatched (manager.py:84)", "live"),
        ("MissionEventStore", "SQLite append_and_publish, source-of-truth", "live"),
        ("startup_recover", "stale non-terminal -> FAILED on boot (recovery.py:23)", "live"),
        ("Kontrollierer.run_mission", "THE orchestrator (orchestrator.py:160)", "live", "m.kontroll"),
        ("MissionDecomposer", "prompt -> MissionPlan 1..5 steps (decomposer.py)", "live"),
        ("critic loop (<=3)", "_run_task_with_critic_loop, MAX_CRITIC_LOOPS=3 (runner.py:271)", "live", "m.loop"),
        ("WorktreeManager", "git worktree add -b agent/<slug>, <=200 cap (worktree.py:114)", "live"),
        ("WindowsJobObject", "KILL_ON_JOB_CLOSE reaps tree (job_object.py:91)", "live"),
        ("build_worker_env", "whitelist env incl OAuth token (isolation/env.py)", "live"),
        ("WorkerProtocol", "spawn()->AsyncIterator contract (workers/base.py:37)", "live"),
        ("SubJarvis / ClaudeDirect / Codex / Gemini worker", "default = claude-cli OAuth, NOT API (claude_direct_worker.py:36)", "live", "m.worker"),
        ("_capture_diff", "git add -N + diff HEAD = ground truth (orchestrator.py:871)", "live"),
        ("WorkerDraftReady", "diff + cost + tokens -> budget (orchestrator.py:1275)", "live"),
        ("BudgetTracker", "per-mission $5 / daily $50, warn 50/80 (budget.py:48)", "live"),
        ("safety scan", "injection_scan + path_guard on diff (orchestrator.py:793)", "live"),
        ("CriticRunner", "out-of-proc reviewer -> CriticVerdict (runner.py:400)", "live", "m.critic"),
        ("empty-diff pre-gate", "deterministic revise, log != evidence (runner.py:499)", "live"),
        ("capability-honesty gate", "no tool-call evidence -> fail (runner.py:193)", "live"),
        ("choose_critic_model", "Sonnet -> Opus on security/low-conf (escalation.py)", "live"),
        ("CriticVerdict", "frozen, 4 axes, approve/revise/reject (verdict.py)", "live", "m.verdict"),
        ("WorkerCorrectionRequired", "revise -> reflection, NEVER voiced (orchestrator.py:1329)", "live"),
        ("ReflectionMemory", "episodic memory between iters (reflections.py)", "live"),
        ("MissionApproved", "Kontrollierer-SIGNED summary_de (orchestrator.py:1121)", "live", "m.approved"),
        ("MissionFailed", "reason + last_state + partial artifacts", "live"),
        ("MissionVoiceListener", "MissionApproved -> TTS readback (voice/listener.py)", "live"),
        ("MissionReadback", "DE/EN, ONLY summary_de, 280 cap (voice/readback.py:185)", "live"),
        ("MissionState SM", "8 states + ALLOWED_TRANSITIONS (state_machine.py:15)", "live"),
    ]

    def content(cx, cy, w):
        return grid(chain, cx, cy, w - 150, 5, gx=22, gy=28)

    Y = room("HEAVY WORK · Phase-6 Mission / Critic loop (self-healing)",
             "spawn_openclaw -> isolated worktree worker -> Critic (<=3 loops) -> Kontrollierer signs -> voice reads ONLY signed summary_de",
             T_MISSION, MARGIN, Y, PAGE_W, content, key="mission") + GAP
    a = ANCHORS
    if "m.loop" in a and "m.verdict" in a:
        # critic loop-back: route up the reserved right channel + row gutters,
        # so it never crosses a card. Enter loop card from its top edge.
        lp = a["m.loop"]
        vd = a["m.verdict"]
        chan_x = MARGIN + 26 + (PAGE_W - 52 - 150) + 60
        off = 14
        arrow(vd["cx"], vd["y"], lp["cx"], lp["y"],
              waypoints=[(vd["cx"], vd["y"] - off),
                         (chan_x, vd["y"] - off),
                         (chan_x, lp["y"] - off),
                         (lp["cx"], lp["y"] - off)],
              color=T_MISSION, sw=3, dashed=True,
              label="revise <=3 (Critic -> Worker)")


def region_memory():
    global Y
    begin_room()
    aware = [
        ("AwarenessManager", "live-state + watcher lifecycle (awareness/manager.py:39)", "mem", "mem.awm"),
        ("AwarenessState (A0)", "current_frame snapshot (awareness/state.py)", "mem"),
        ("Watchers", "WindowFocus + Idle (awareness/watchers/)", "mem"),
        ("StoryTracker (A2)", "episodes from FrameUpdated (awareness/story.py:113)", "mem"),
        ("SalienceScorer", "scores frames for episodes (salience.py:42)", "mem"),
        ("Verdichter", "condenses frames -> summaries (verdichter.py:48)", "mem"),
        ("WorkingSet (A4)", "RAM-only multi-context LRU (working_set.py:31)", "mem"),
        ("EpisodeBuilder (A3)", "Episode -> awareness_episodes (episode.py:24)", "mem"),
        ("Probes (A5)", "git/fs probes, 0.2s cap (probes/)", "mem"),
        ("RecallStore", "SQLite awareness_episodes_fts (memory/recall.py)", "mem"),
    ]
    wiki = [
        ("wiki bootstrap", "wires curator + rollup, vault_root (wiki/integration.py)", "mem", "mem.wiki"),
        ("WikiCurator (B1)", "ingest facts into vault (wiki/curator.py)", "mem"),
        ("AtomicWriter", "ALL vault writes flow here (wiki/atomic_writer.py)", "mem"),
        ("PageRepository", "page read/write over vault md", "mem"),
        ("VaultSearch", "FTS5 wiki_fts in jarvis.db (wiki/search.py)", "mem"),
        ("SessionRollupWorker (B7)", "rolls session->vault on IdleEntered", "mem"),
        ("VoiceFactBridge", "transcript -> async fact ingest (wiki/voice_bridge.py:85)", "mem"),
        ("CuratorScheduler", "lock + cooldown + fallback ingest (scheduler.py)", "partial"),
        ("CoreMemory", "always-in-prompt persona + facts (core_memory.py:36)", "mem"),
        ("legacy Curator (B4)", "soft-disabled, data/workspace/ frozen", "open"),
    ]

    def content(cx, cy, w):
        text(cx, cy, "AWARENESS A0-A5 (off the voice critical path)", fs=15,
             color=T_MEM, font=FONT_HEAD)
        b1 = grid(aware, cx, cy + 28, w, 5, gx=20, gy=16)
        text(cx, b1 + 18, "KNOWLEDGE WIKI B0-B9 (+ core memory)", fs=15,
             color=T_MEM, font=FONT_HEAD)
        b2 = grid(wiki, cx, b1 + 46, w, 5, gx=20, gy=16)
        return b2

    Y = room("MEMORY TIERS — what Jarvis remembers",
             "Awareness (live working memory) + Knowledge Wiki (long-term vault) + Core memory (always-in-prompt)",
             T_MEM, MARGIN, Y, PAGE_W, content, key="memory") + GAP


def region_features():
    global Y
    begin_room()
    groups = [
        ("Ack-Brain (sub-second butler ACK)", [
            ("AckGenerator.run()", "11-step, NEVER raises (ack_brain/generator.py:231)", "live"),
            ("suppress-if-fast gate", "2000ms poll, hush if brain faster (pipeline.py:1216)", "live"),
            ("UI preamble bubble", "AnnouncementRequested kind=preamble (pipeline.py:1243)", "live"),
            ("4 providers / follow_brain", "mirrors brain.primary (ack_brain/config.py:21)", "live"),
            ("circuit breaker", "threshold 3, cooldown 60s (generator.py:274)", "live"),
        ]),
        ("Optimistic Execution & 'Oops' (AD-OE1..6)", [
            ("AD-OE1 ACK-before-dispatch", "router still synchronous (README:23)", "partial"),
            ("AD-OE2 no MCP await on voice", "in-proc EventBus is the queue", "live"),
            ("AD-OE3 dumb tools in-process", "allowlist gaps BUG-020", "partial"),
            ("AD-OE4 worker issues MCP call", "Welle 2/3 open, not wired E2E", "open"),
            ("AD-OE5 closed Oops loop", "no error->inject->VAD-gated correct", "open"),
            ("AD-OE6 zero silent drops", "always-speak guards live, taxonomy partial", "partial"),
            ("LatencyTracker (Wave 0)", "fire-and-forget spans, NO p95 CI gate (telemetry/latency.py:35)", "partial"),
        ]),
        ("OpenClaw bridge (harness)", [
            ("OpenClawHarness plugin", "registered but DORMANT live_mode=False (openclaw.py:164)", "partial"),
            ("Welle 1 spike", "provider_map + spawn-arg done", "live"),
            ("Welle 2 live bridge", "invoke() returns mock string (openclaw.py:416)", "open"),
            ("Welle 3 live subprocess", "factory+JobObject exist, never armed", "partial"),
            ("Welle 4 sub-jarvis deleted", "only 'router' tier (factory.py:121)", "live"),
            ("ACTUAL worker = claude-cli", "voice uses claude_direct_worker, not the plugin", "live"),
        ]),
        ("Computer-Use (POAV)", [
            ("POAV harness", "the only live CU engine (computer_use_loop.py)", "live"),
        ]),
        ("Self-Mod Phase 7 (atomic config)", [
            ("AtomicConfigWriter", "11-step pipeline (self_mod/writer.py:77)", "live"),
            ("Pre-Validate", "JarvisConfig.model_validate (writer.py:262)", "live"),
            ("sync reload-test", "crash -> restore from backup (writer.py:307)", "live"),
            ("backup + GC", "outside watchdog scope (writer.py:458)", "live"),
            ("3 router-tier tools", "list/get/set_config_value (self_mod_tools.py)", "live"),
            ("spawn_skill_author (7.5)", "NOT in entry-points", "open"),
        ]),
        ("Marketplace <-> MCP  (DECOUPLED)", [
            ("5 plugins / 5 auth modes", "GitHub/Vercel/Notion/Slack/Supabase (catalog.py:87)", "partial"),
            ("mcp_server field", "declarative, route layer never consumes (catalog.py:103)", "open"),
            ("MCP subsystem", "registry + adapter + mcp.json (mcp/registry.py:56)", "live"),
            ("connect saves token only", "never starts an MCP (marketplace_routes.py:144)", "partial"),
            ("ZERO imports between pkgs", "marketplace !-> mcp (grep verified)", "open", "feat.decouple"),
        ]),
        ("CLI catalog + terminal", [
            ("CLI catalog subsystem", "loader+registry+installer+prober (jarvis/clis/)", "live"),
            ("cli-tools virtual loader", "expand -> N CliTool (loader.py:21)", "live"),
            ("CliTool (1 per CLI)", "binary-guard + ENV + usage-log (clis/tool.py:33)", "live"),
            ("spawn-cli-worker", "entry-point -> MISSING module (pyproject:184)", "open"),
            ("Terminal (ConPTY)", "pywinpty (terminal/pty_manager.py)", "live"),
        ]),
    ]

    def content(cx, cy, w):
        yy = cy
        colw = (w - 2 * 28) / 3
        # 3-column flow of group panels
        col = 0
        colx = [cx, cx + colw + 28, cx + 2 * (colw + 28)]
        coly = [yy, yy, yy]
        for gtitle, cards in groups:
            x = colx[col]
            y0 = coly[col]
            text(x, y0, gtitle, fs=14, color=T_FEAT, font=FONT_HEAD)
            yb = grid(cards, x, y0 + 26, colw, 1, gx=0, gy=12)
            coly[col] = yb + 34
            col = (col + 1) % 3
            # keep columns balanced: choose shortest next time
            col = coly.index(min(coly))
        return max(coly)

    Y = room("NEW FEATURE SUBSYSTEMS — live vs partial vs open (the honest status board)",
             "What is really wired in today. AMBER = built but dormant/gated. RED = open or dead wiring.",
             T_FEAT, MARGIN, Y, PAGE_W, content) + GAP


def region_output():
    global Y
    begin_room()
    out = [
        ("brain output (stream)", "generate_stream tokens (manager.py)", "live", "o.brain"),
        ("_brain_streaming", "per-sentence scrub->speak (pipeline.py:2376)", "live"),
        ("_brain_with_ack", "non-stream + task-ack timer (pipeline.py:2462)", "live"),
        ("scrub_for_voice", "regex-only, NO LLM (output_filter.py:330)", "live", "o.scrub"),
        ("WHITELIST_WORDS", "Datei/Email/Browser/Terminal/Notiz/Termin/Kalender (output_filter.py:39)", "live"),
        ("_speak() + barge race", "tts.synthesize -> play_chunks (pipeline.py:2512)", "live", "o.speak"),
        ("_barge_monitor", "2nd mic, Silero 0.97x12, 1.5s grace (pipeline.py:2573)", "live"),
        ("_on_announcement", "bus TTS bypass, re-scrubs (pipeline.py:1093)", "live"),
        ("_on_background_completed", "proactive worker-done readback (pipeline.py:1249)", "live"),
        ("GeminiFlashTTS (Charon)", "24kHz PCM, 429 sibling-bridge (gemini_flash_tts.py:85)", "live", "o.gem"),
        ("GrokVoiceTTS (leo)", "POST /v1/tts, fallback->Gemini->SAPI5 (grok_voice_tts.py:74)", "live", "o.grok"),
        ("SAPI5 emergency", "allow_sapi5_fallback=False default, silence-on-fail", "partial"),
        ("AudioPlayer (WASAPI out)", "persistent stream, float32 (audio/player.py:231)", "live", "o.player"),
        ("_resolve_output_device", "shortest-token 'PRO X', blocks HDMI (player.py:147)", "live"),
        ("WDM-KS forbidden filter", "BUG-014, PaErrorCode -9999 (player.py:71)", "live"),
        ("rate cascade + hot-swap", "48k->44.1k->source, USB hot-swap (player.py:382)", "live"),
        ("AudioPlayer.stop", "abort() instant discard (barge/hangup) (player.py:613)", "live"),
        ("AudioOutFirst event", "first sample -> orb mouth/SPEAKING (player.py:595)", "live"),
        ("chime / disconnect tone", "880+1320Hz wake, 660->440 hangup (audio/chime.py)", "live"),
        ("_warmup 12-90s", "loads Whisper/Silero/OWW/TTS, prerender acks (pipeline.py:1506)", "live"),
    ]

    def content(cx, cy, w):
        return grid(out, cx, cy, w, 5, gx=22, gy=18)

    Y = room("L2/L1 · Speech OUTPUT + Audio I/O",
             "brain -> scrub_for_voice (whitelist) -> TTS (Gemini/Grok, SAPI5 off) -> WASAPI player (WDM-KS blocked)",
             T_AUDIO, MARGIN, Y, PAGE_W, content) + GAP
    # (no diagonal intra-region connector)


def region_bus():
    global Y
    begin_room()
    evs = [
        ("EventBus (asyncio)", "topic + wildcard subs (core/bus.py:23)", "evt"),
        ("subscribe_all / flight-recorder", "wildcard gets every event (bus.py:35)", "evt"),
        ("_safe_dispatch swallow", "subscriber exc logged not propagated (bus.py:64, AP-18)", "evt"),
        ("Event(frozen, slots)", "trace_id:UUID + timestamp_ns (events.py:30)", "evt"),
        ("WakeWordDetected", "events.py:47", "evt"),
        ("TranscriptFinal", "events.py:70", "evt"),
        ("IntentClassified", "intent + risk_tier (events.py:85)", "evt"),
        ("ActionProposed/Approved/Executed", "tool lifecycle (events.py:125)", "evt"),
        ("ToolCallStarted/Completed", "tool dispatch telemetry (events.py:809)", "evt"),
        ("ResponseGenerated", "brain output text+lang (events.py:178)", "evt"),
        ("OpenClawTaskStarted/Completed", "worker mission lifecycle (events.py:651)", "evt"),
        ("OpenClawBackgroundCompleted", "proactive readback trigger (events.py:824)", "evt"),
        ("AnnouncementRequested", "preamble/info -> TTS (events.py:296)", "evt"),
        ("SystemStateChanged", "IDLE/LISTENING/THINKING/SPEAKING (events.py:226)", "evt"),
        ("BrainProviderSwitched", "runtime switch (events.py:92)", "evt"),
        ("KillRequested/Acknowledged", "emergency stop, ADR-0004 (events.py:354)", "evt"),
        ("ConfigReloaded", "self-mod atomic write done", "evt"),
        ("AudioOutFirst", "first audible sample (player.py:595)", "evt"),
    ]

    def content(cx, cy, w):
        return grid(evs, cx, cy, w, 5, gx=20, gy=16)

    Y = room("EVENT BUS — the lateral spine (frozen events, trace_id, flight-recorder)",
             "Layers talk only via protocols (down) and typed frozen events on the bus (lateral). A broken subscriber never propagates.",
             T_EVT, MARGIN, Y, PAGE_W, content) + GAP


def evidence_panel():
    """Dark code-snippet evidence artifacts."""
    global Y
    begin_room()
    snippets = [
        ("ROUTER_TOOLS frozenset  (jarvis/brain/factory.py:40)",
         'ROUTER_TOOLS = frozenset({\n  "run-shell","screen-snapshot","dispatch-to-harness",\n'
         '  "multi-spawn","spawn-openclaw","dispatch-with-review",\n'
         '  "awareness-snapshot","awareness-recall","run-skill",\n'
         '  "wiki-recall","wiki-page-read","wiki-ingest"})\n'
         '# + 3 self-mod tools = 16 live router tools'),
        ("force-spawn heuristic  (manager.py:1155)",
         'if smalltalk_re.search(t): return False\n'
         'if mode == "strict":\n'
         '    return bool(force_spawn_pattern.search(t))\n'
         'if verb_re.search(t): return True\n'
         'if marker_re.search(t): return True'),
        ("MAX_CRITIC_LOOPS  (critic/runner.py:271)",
         'MAX_CRITIC_LOOPS: Final[int] = 3\n'
         '"""Hardcoded per ADR-0009. Not configurable\n'
         'without a new decision record."""'),
        ("scrub whitelist (sacred)  (output_filter.py:39)",
         'WHITELIST_WORDS = (\n'
         '  "Datei","Email","Browser","Terminal",\n'
         '  "Notiz","Termin","Kalender")'),
        ("suppress-if-fast ACK gate  (pipeline.py:1216)",
         'for _ in range(poll_steps):\n'
         '  await asyncio.sleep(poll_step_s)\n'
         '  if turn_state in (JARVIS_SPEAKING, LISTENING):\n'
         '    return   # brain answered faster -> hush ack'),
        ("Marketplace <-> MCP decoupling  (catalog.py:103)",
         '# mcp_server: the route layer does NOT\n'
         '# consume this today. connect_pat only does\n'
         'store.save(plugin_id, Tokens(...))\n'
         '# -> connecting a plugin starts NO MCP server'),
        ("WDM-KS forbidden host-API  (audio/player.py:71)",
         '_FORBIDDEN_OUTPUT_HOSTAPIS = frozenset({\n'
         '  "Windows WDM-KS"})  # PortAudio blocking\n'
         '# write API crashes there (BUG-014, -9999)'),
    ]

    def content(cx, cy, w):
        colw = 500
        ncols = 3
        gx, gy = 30, 26
        col = 0
        rowy = cy
        rowh = 0
        for title_s, code in snippets:
            if col == ncols:
                rowy += rowh + gy
                rowh = 0
                col = 0
            x = cx + col * (colw + gx)
            lines = code.split("\n")
            h = 40 + len(lines) * 13 * 1.3 + 16
            rect(x, rowy, colw, h, fill="#0e0d0c", stroke="#e7c46e", sw=2, round_type=3)
            text(x + 14, rowy + 10, title_s, fs=11, color="#e7c46e", font=FONT_BODY)
            text(x + 14, rowy + 32, code, fs=13, color="#c9d1d9", font=3)
            rowh = max(rowh, h)
            col += 1
        return rowy + rowh

    Y = room("EVIDENCE ARTIFACTS — verbatim from the code (proof, not labels)",
             "The load-bearing literals behind the diagram.",
             "#e7c46e", MARGIN, Y, PAGE_W, content) + GAP


def region_gap_connectors():
    """Short, centered, vertical connectors in the gap between stacked regions.

    No long cross-poster lines — each arrow only spans one inter-region gap,
    forming a clean top-to-bottom data-flow spine.
    """
    seq = [
        ("input", "core", "transcript -> brain"),
        ("core", "flow", "generate() sequence"),
        ("flow", "catalog", "ROUTER_TOOLS tool-use"),
        ("catalog", "mission", "spawn-openclaw -> Mission"),
        ("mission", "memory", "facts written / recalled"),
    ]
    for a_, b_, lab in seq:
        if a_ in REGION_BOUNDS and b_ in REGION_BOUNDS:
            cx_a, _, bottom_a = REGION_BOUNDS[a_]
            cx_b, top_b, _ = REGION_BOUNDS[b_]
            x = (cx_a + cx_b) / 2
            arrow(x, bottom_a + 6, x, top_b - 6, color="#343a40", sw=4,
                  label=lab)


# --------------------------------------------------------------------------
# assemble
# --------------------------------------------------------------------------
def build():
    hrule_title()
    legend()
    region_ui()
    region_input()
    region_core()
    region_flow()
    region_catalog()
    region_mission()
    region_memory()
    region_features()
    region_output()
    region_bus()
    evidence_panel()
    region_gap_connectors()

    doc = {
        "type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
        "elements": ELEMENTS,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": 20},
        "files": {},
    }
    out_dir = "<USER_HOME>/OneDrive/Ex/2026-05-24"
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "jarvis-tool-architecture.excalidraw")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    print(f"elements: {len(ELEMENTS)}")
    print(f"written: {out}")


if __name__ == "__main__":
    build()
