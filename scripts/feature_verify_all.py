"""Orchestrated self-verification of Jarvis features against the LIVE app.

Drives each remaining feature by calling feature_verify_probe.py as a subprocess
(fresh WS per prompt), and adds:
  * mission outcome polling (missions.db) for sub-agent cases,
  * Calculator process delta + screenshot for the computer-use case,
  * provider-switch verification (BrainTurnStarted.provider).

Writes a JSON report to scripts/_verify_report.json and a human summary to stdout.
Designed to run in the background; safe to re-run.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = str(REPO / "scripts" / "feature_verify_probe.py")
PY = sys.executable


def run_probe(prompt: str, secs: float, idle: float) -> dict:
    """Call the single-prompt probe, return its parsed VERDICT dict."""
    try:
        cp = subprocess.run(
            [PY, PROBE, prompt, "--secs", str(secs), "--idle", str(idle)],
            capture_output=True, text=True, timeout=secs + 60, cwd=str(REPO),
        )
    except subprocess.TimeoutExpired:
        return {"prompt": prompt, "error": "probe_timeout"}
    out = (cp.stderr or "") + "\n" + (cp.stdout or "")
    m = re.search(r"^VERDICT (\{.*\})\s*$", out, re.MULTILINE)
    if not m:
        return {"prompt": prompt, "error": "no_verdict", "raw": out[-400:]}
    try:
        return json.loads(m.group(1))
    except Exception as exc:  # noqa: BLE001
        return {"prompt": prompt, "error": f"parse: {exc}"}


def _missions_after(baseline_ms: int) -> list[dict]:
    try:
        c = sqlite3.connect("file:data/missions.db?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id,prompt,state,created_ms,updated_ms FROM missions "
            "WHERE created_ms > ? ORDER BY created_ms ASC", (baseline_ms,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        return [{"err": str(exc)}]


def _mission_reason(mid: str) -> str:
    try:
        c = sqlite3.connect("file:data/missions.db?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT event_type,payload_json FROM mission_events "
            "WHERE mission_id=? ORDER BY seq DESC LIMIT 6", (mid,),
        ).fetchall()
        bits = []
        for r in rows:
            try:
                p = json.loads(r["payload_json"] or "{}")
            except Exception:  # noqa: BLE001
                p = {}
            for k in ("reason", "summary", "summary_de", "correction_instruction"):
                if p.get(k):
                    bits.append(f"{r['event_type']}.{k}={str(p[k])[:160]}")
                    break
        return " | ".join(bits[:4])
    except Exception as exc:  # noqa: BLE001
        return f"ERR {exc}"


def _max_created() -> int:
    try:
        c = sqlite3.connect("file:data/missions.db?mode=ro", uri=True)
        r = c.execute("SELECT MAX(created_ms) FROM missions").fetchone()[0]
        return int(r or 0)
    except Exception:  # noqa: BLE001
        return 0


def mission_case(label: str, prompt: str, wait_s: float = 200.0) -> dict:
    base = _max_created()
    verdict = run_probe(prompt, secs=35, idle=15)
    # poll for a new mission reaching terminal state
    terminal = {"APPROVED", "FAILED", "REJECTED", "CANCELLED"}
    deadline = time.monotonic() + wait_s
    mission = None
    while time.monotonic() < deadline:
        ms = _missions_after(base)
        if ms:
            mission = ms[-1]
            if str(mission.get("state", "")).upper() in terminal:
                break
        time.sleep(5)
    result = {"label": label, "verdict": verdict, "spawned": bool(mission)}
    if mission:
        result["mission_state"] = mission.get("state")
        result["mission_reason"] = _mission_reason(mission["id"])
        result["loops"] = None
    return result


def _calc_pids() -> int:
    try:
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process CalculatorApp,Calculator,ApplicationFrameHost "
             "-ErrorAction SilentlyContinue | Measure-Object).Count"],
            capture_output=True, text=True, timeout=20,
        )
        return int((cp.stdout or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return -1


def _screenshot(name: str) -> str:
    out = f"screenshots/{name}"
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.X,$b.Y,0,0,$bmp.Size);"
        f"$bmp.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass
    return out


def cu_case(label: str, prompt: str) -> dict:
    before = _calc_pids()
    verdict = run_probe(prompt, secs=70, idle=25)
    time.sleep(5)
    after = _calc_pids()
    shot = _screenshot(f"cu_{label}.png")
    return {"label": label, "verdict": verdict, "calc_before": before,
            "calc_after": after, "opened": after > before, "screenshot": shot}


def main() -> int:
    report: dict = {"started": None, "cases": []}

    # --- Computer-use (Calculator: clean process delta) ---
    report["cases"].append(cu_case("calculator", "Öffne den Rechner."))  # i18n-allow: simulated German user command driving a live computer-use probe

    # --- Sub-agent missions ---
    report["cases"].append(mission_case(
        "mission_file",
        "Schreib eine 120-Wörter-Geschichte über einen Roboter namens Beacon "  # i18n-allow: simulated German user command driving a live mission-spawn probe
        "und speichere sie in eine Datei."))  # i18n-allow: simulated German user command driving a live mission-spawn probe
    report["cases"].append(mission_case(
        "mission_answer", "Which city would you recommend for a trip to Australia?"))
    report["cases"].append(mission_case(
        "mission_impossible", "Book me a trip from Lisbon to Tokyo."))

    # --- Provider switch ---
    sw1 = run_probe("Wechsel zu Grok.", secs=40, idle=15)
    chk = run_probe("Sag bitte kurz hallo.", secs=40, idle=15)
    sw2 = run_probe("Wechsel zurück zu Gemini.", secs=40, idle=15)  # i18n-allow: simulated German user command driving a live provider-switch probe
    report["cases"].append({"label": "provider_switch", "switch_to_grok": sw1,
                            "after_switch_provider": chk.get("provider"),
                            "after_switch_verdict": chk, "switch_back": sw2})

    Path("scripts/_verify_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # human summary
    print("=" * 70)
    for c in report["cases"]:
        print(json.dumps(c, ensure_ascii=False)[:600])
    print("REPORT WRITTEN: scripts/_verify_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
