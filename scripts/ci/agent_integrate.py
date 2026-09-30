#!/usr/bin/env python3
"""Bring a branch up to date with main and resolve merge conflicts on its own.

This is the engine behind both halves of agent integration:

* the **merge train** (``.github/workflows/merge-train.yml`` -> ``train``)
  that keeps every open agent pull request current with main and lands the
  green ones one at a time, and
* ``scripts/agent_land.py``, which a coding agent runs locally before it
  pushes.

Conflicts are resolved by file class, cheapest first:

1. **Generated files** (frontend ``dist/``, the .claude / .codex copies of
   .agents, the CLI reference docs, lockfiles, timing caches): take main's
   copy, then REGENERATE from the merged sources. A hand-merge of generated
   output is always wrong; a regeneration is always right.
2. **Append-only files** (CHANGELOG.md, allowlists, .gitignore): a union
   merge keeps both sides' lines.
3. **Everything else**: handed to an optional resolver command
   (``--resolver-cmd``, e.g. ``claude -p`` or ``codex exec -``) that edits
   the files in place. Its output must leave no conflict markers and must
   still parse (Python compiles, JSON loads) — otherwise the update aborts.
4. Anything still unresolved aborts cleanly and is reported, so a human or
   the authoring agent resolves it. Nothing half-merged is ever committed.

``git rerere`` is on throughout, so a resolution recorded once is replayed.

Usage::

    agent_integrate.py update --onto origin/main [--mode merge|rebase]
    agent_integrate.py train --repo OWNER/NAME [--dry-run]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import py_compile
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = "jarvis/ui/web/frontend"

# (glob, regenerator key). Main's copy is taken, then the key regenerates it.
GENERATED: tuple[tuple[str, str], ...] = (
    ("jarvis/ui/web/dist/*", "frontend-build"),
    # AGENTS.md and .agents/ are hand-written sources, never taken from main.
    (".claude/agents/*", "mirrors"),
    (".claude/commands/*", "mirrors"),
    (".claude/skills/*", "mirrors"),
    (".codex/agents/*", "mirrors"),
    ("docs/jarvis-cli-reference.md", "reference-docs"),
    ("docs/commands-reference.md", "reference-docs"),
    (f"{FRONTEND}/package-lock.json", "npm-lock"),
    ("uv.lock", "uv-lock"),
    ("requirements.txt", "requirements"),
    ("test_durations.json", ""),
    ("scripts/ci/test-baseline-*.json", ""),
)
UNION: tuple[str, ...] = (
    "CHANGELOG.md",
    "scripts/ci/german-allowlist.txt",
    ".gitignore",
    "docs/BUGS.md",
)
AGENT_BRANCH_PREFIXES = ("codex/", "claude/", "agent/", "agents/", "gemini/", "cursor/", "bot/")
OPT_OUT_LABELS = {"no-auto-merge", "do-not-merge", "wip", "needs-human"}
OPT_IN_LABEL = "auto-merge"


def git(*args: str, check: bool = True, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _sh(cmd: list[str], *, cwd: Path = REPO_ROOT, stdin: str | None = None) -> int:
    print(f"[integrate] $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        input=stdin,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode


def classify(path: str) -> tuple[str, str]:
    """Return (strategy, regenerator) for a conflicted path."""
    for pattern, regen in GENERATED:
        if fnmatch.fnmatch(path, pattern):
            return "generated", regen
    if path in UNION:
        return "union", ""
    return "semantic", ""


def conflicted_files(cwd: Path = REPO_ROOT) -> list[str]:
    out = git("diff", "--name-only", "--diff-filter=U", cwd=cwd).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def has_markers(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return text.startswith("<<<<<<< ") or any(m in text for m in ("\n<<<<<<< ", "\n>>>>>>> "))


def still_valid(path: Path) -> str | None:
    """Return an error when a resolved file no longer parses."""
    if not path.exists():
        return None
    if has_markers(path):
        return "conflict markers remain"
    if path.suffix == ".py":
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            return f"does not compile: {exc.msg.strip()[:200]}"
    if path.suffix == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return f"invalid JSON: {exc}"
    return None


def _stage_blob(stage: int, path: str, cwd: Path) -> str | None:
    result = git("show", f":{stage}:{path}", check=False, cwd=cwd)
    return result.stdout if result.returncode == 0 else None


def union_merge(path: str, cwd: Path) -> bool:
    base = _stage_blob(1, path, cwd) or ""
    ours = _stage_blob(2, path, cwd)
    theirs = _stage_blob(3, path, cwd)
    if ours is None or theirs is None:
        return False
    tmp = cwd / ".git-union-tmp"
    tmp.mkdir(exist_ok=True)
    try:
        files = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            target = tmp / name
            target.write_text(text, encoding="utf-8", newline="")
            files.append(str(target))
        proc = subprocess.run(  # noqa: S603
            ["git", "merge-file", "--union", "-p", *files],  # noqa: S607
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            cwd=cwd,
        )
        (cwd / path).write_text(proc.stdout, encoding="utf-8", newline="")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    git("add", "--", path, cwd=cwd)
    return True


def take_side(path: str, side: str, cwd: Path) -> None:
    """Check out one side of a conflict; a side that deleted the file deletes it."""
    result = git("checkout", f"--{side}", "--", path, check=False, cwd=cwd)
    if result.returncode != 0:
        git("rm", "-q", "--cached", "--ignore-unmatch", "--", path, check=False, cwd=cwd)
        (cwd / path).unlink(missing_ok=True)
        return
    git("add", "--", path, cwd=cwd)


def regenerate(keys: set[str], cwd: Path) -> list[str]:
    """Run the regenerators; return the ones that failed."""
    failures: list[str] = []
    py = sys.executable
    commands: dict[str, list[list[str]]] = {
        "mirrors": [
            [py, "scripts/ci/sync_agents_dir.py"],
            [py, "scripts/ci/sync_codex_agents.py"],
        ],
        "reference-docs": [
            [py, "scripts/ci/gen_cli_reference.py"],
            [py, "scripts/ci/gen_commands_reference.py"],
        ],
        "uv-lock": [["uv", "lock"]],
        "requirements": [
            [
                "uv",
                "pip",
                "compile",
                "--universal",
                "--generate-hashes",
                "--python-version",
                "3.11",
                "--output-file=requirements.txt",
                "requirements.in",
            ]
        ],
    }
    npm = shutil.which("npm") or "npm"
    commands["npm-lock"] = [[npm, "install", "--package-lock-only", "--ignore-scripts"]]
    commands["frontend-build"] = [[npm, "ci"], [npm, "run", "build"]]
    order = ["npm-lock", "uv-lock", "requirements", "mirrors", "reference-docs", "frontend-build"]
    for key in order:
        if key not in keys:
            continue
        workdir = cwd / FRONTEND if key in {"npm-lock", "frontend-build"} else cwd
        tool = commands[key][0][0]
        if shutil.which(tool) is None and not Path(tool).exists():
            print(f"[integrate] regenerator {key}: {tool} not installed", flush=True)
            failures.append(key)
            continue
        for cmd in commands[key]:
            if _sh(cmd, cwd=workdir) != 0:
                failures.append(key)
                break
    return failures


@dataclass
class Resolution:
    resolved: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    regenerate: set[str] = field(default_factory=set)
    ai_resolved: list[str] = field(default_factory=list)


def resolve_conflicts(main_side: str, resolver_cmd: str, cwd: Path) -> Resolution:
    """Resolve the current index conflicts. ``main_side`` is ours|theirs."""
    res = Resolution()
    semantic: list[str] = []
    for path in conflicted_files(cwd):
        strategy, regen = classify(path)
        if strategy == "generated":
            take_side(path, main_side, cwd)
            if regen:
                res.regenerate.add(regen)
            res.resolved.append(path)
        elif strategy == "union" and union_merge(path, cwd):
            res.resolved.append(path)
        else:
            semantic.append(path)
    # rerere may already have replayed a recorded resolution: both sides still
    # exist and the markers are gone. A modify/delete conflict also has no
    # markers, but one side is missing — that one stays unresolved.
    for path in list(semantic):
        both_sides = _stage_blob(2, path, cwd) is not None and _stage_blob(3, path, cwd) is not None
        if both_sides and (cwd / path).exists() and still_valid(cwd / path) is None:
            git("add", "--", path, cwd=cwd)
            semantic.remove(path)
            res.resolved.append(path)
    if semantic and resolver_cmd:
        prompt = build_prompt(semantic, main_side)
        _sh([*shlex.split(resolver_cmd)], cwd=cwd, stdin=prompt)
        for path in list(semantic):
            error = still_valid(cwd / path)
            if error is None:
                git("add", "--", path, cwd=cwd)
                semantic.remove(path)
                res.resolved.append(path)
                res.ai_resolved.append(path)
            else:
                print(f"[integrate] resolver left {path} broken: {error}", flush=True)
    res.unresolved = semantic
    return res


def build_prompt(paths: list[str], main_side: str) -> str:
    branch_side = "theirs" if main_side == "ours" else "ours"
    listing = "\n".join(f"- {p}" for p in paths)
    return (
        "You are resolving git merge conflicts in the Personal Jarvis repository. "
        "The files below contain conflict markers. For each file, edit it in place so "
        "that it keeps the intent of BOTH sides: the main branch "
        f"(git side '{main_side}') is the latest shared state; the feature branch "
        f"(git side '{branch_side}') carries new work that must survive on top of it. "
        "Remove every conflict marker. Do not touch any other file, do not run git, "
        "do not commit. If a conflict cannot be resolved safely, leave its markers in "
        "place — an unresolved file is reported to a human, a wrong guess is not.\n\n"
        f"Files:\n{listing}\n"
    )


def _in_progress(cwd: Path) -> str | None:
    git_dir = Path(git("rev-parse", "--git-dir", cwd=cwd).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = cwd / git_dir
    if (git_dir / "MERGE_HEAD").exists():
        return "merge"
    if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
        return "rebase"
    return None


def update(onto: str, mode: str, resolver_cmd: str, cwd: Path = REPO_ROOT) -> dict[str, object]:
    """Update HEAD with ``onto``. Returns a JSON-able report; ``status`` is
    ``up-to-date`` | ``updated`` | ``conflict``."""
    if git("merge-base", "--is-ancestor", onto, "HEAD", check=False, cwd=cwd).returncode == 0:
        return {"status": "up-to-date"}
    report: dict[str, object] = {"status": "updated", "resolved": [], "ai_resolved": []}
    regen: set[str] = set()
    resolved: list[str] = []
    ai: list[str] = []
    if mode == "merge":
        result = git("merge", "--no-edit", "--no-ff", onto, check=False, cwd=cwd)
        if result.returncode != 0:
            res = resolve_conflicts("theirs", resolver_cmd, cwd)
            resolved += res.resolved
            ai += res.ai_resolved
            regen |= res.regenerate
            if res.unresolved or conflicted_files(cwd):
                git("merge", "--abort", check=False, cwd=cwd)
                return {"status": "conflict", "unresolved": res.unresolved or conflicted_files(cwd)}
            git("commit", "--no-edit", check=False, cwd=cwd)
    else:
        git("rebase", onto, check=False, cwd=cwd)
        guard = 0
        while _in_progress(cwd) == "rebase":
            guard += 1
            res = resolve_conflicts("ours", resolver_cmd, cwd)
            resolved += res.resolved
            ai += res.ai_resolved
            regen |= res.regenerate
            if res.unresolved or conflicted_files(cwd) or guard > 200:
                git("rebase", "--abort", check=False, cwd=cwd)
                return {"status": "conflict", "unresolved": res.unresolved or conflicted_files(cwd)}
            env_result = subprocess.run(  # noqa: S603
                ["git", "-c", "core.editor=true", "rebase", "--continue"],  # noqa: S607
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            if env_result.returncode != 0 and not conflicted_files(cwd):
                # An emptied commit: its change already landed on main.
                git("rebase", "--skip", check=False, cwd=cwd)
    if regen:
        failed = regenerate(regen, cwd)
        # One missing pathspec makes `git add` stage NOTHING, so pass only the
        # generated paths that exist — a half-staged frontend bundle is the
        # exact breakage the dist-consistency gate exists for.
        specs = {p.rstrip("*").rstrip("/") for p, _ in GENERATED}
        existing = sorted(s for s in specs if (cwd / s).exists())
        if existing:
            git("add", "-A", "--", *existing, cwd=cwd)
        staged = git("diff", "--cached", "--name-only", cwd=cwd).stdout.strip()
        if staged:
            commit = git(
                "commit",
                "-m",
                "chore: regenerate generated files after update",
                check=False,
                cwd=cwd,
            )
            if commit.returncode != 0:
                print(commit.stdout[-3000:] + commit.stderr[-3000:], flush=True)
                return {"status": "conflict", "unresolved": ["<regenerated files rejected>"]}
        report["regenerated"] = sorted(regen - set(failed))
        if failed:
            report["regen_failed"] = failed
    report["resolved"] = sorted(set(resolved))
    report["ai_resolved"] = sorted(set(ai))
    return report


# --------------------------------------------------------------------------- merge train


def gh_json(*args: str) -> object:
    out = subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return json.loads(out or "null")


def gh(*args: str) -> int:
    print(f"[train] $ gh {' '.join(args)}", flush=True)
    return subprocess.run(["gh", *args], check=False).returncode  # noqa: S603, S607


def eligible(pr: dict) -> bool:
    labels = {label["name"] for label in pr.get("labels", [])}
    if pr.get("isDraft") or pr.get("baseRefName") != "main" or labels & OPT_OUT_LABELS:
        return False
    if pr.get("isCrossRepository"):
        return False  # a fork's code never runs with this workflow's write token
    return OPT_IN_LABEL in labels or pr.get("headRefName", "").startswith(AGENT_BRANCH_PREFIXES)


_ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}


def run_state(runs: list[dict]) -> tuple[str, int | None]:
    """(state, run id) for a pull request's head commit, from its ci.yml runs.

    Only ``pull_request`` runs count: GitHub attaches the `CI gate` check of a
    workflow_dispatch run to the commit but NOT to the pull request, so branch
    protection never sees it. A pull_request run triggered by the train's own
    GITHUB_TOKEN push parks in ``action_required``; the train approves it.

    Read from the workflow RUNS, not the `CI gate` check: the gate is the last
    job, so a running CI has no gate check yet.

    States: pending | success | failure | approve | rerun | missing.
    """
    pr_runs = [r for r in runs if r.get("event") == "pull_request"]
    if any(r.get("status") in _ACTIVE for r in pr_runs):
        return "pending", None
    if not pr_runs:
        return "missing", None
    latest = max(pr_runs, key=lambda r: r.get("created_at") or "")
    conclusion = latest.get("conclusion")
    run_id = latest.get("id")
    if conclusion == "success":
        return "success", run_id
    if conclusion == "action_required":
        return "approve", run_id
    if conclusion in ("cancelled", "stale", "skipped", "neutral", None):
        return "rerun", run_id
    return "failure", run_id


def gate_state(repo: str, sha: str) -> tuple[str, int | None]:
    data = gh_json("api", f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=50")
    runs = (data or {}).get("workflow_runs", []) if isinstance(data, dict) else []
    return run_state(runs)


def dispatch_ci(ref: str) -> None:
    gh("workflow", "run", "ci.yml", "--ref", ref, "-f", "full=false")


def decide(mergeable: str, state: str, behind: bool) -> str:
    """The train's action for one pull request.

    Mirrors GitHub's non-strict model (and Hermes Agent's): a branch is NOT
    brought up to date just because main moved — with several agents pushing
    to main, that re-ran every PR's CI on every push and nothing ever landed.
    A branch is updated only when it CONFLICTS with main (or its last CI failed
    while it was behind, since a newer main may be the fix). A green,
    conflict-free PR merges; main's full post-merge run is the backstop for
    changes that are fine alone and break together.
    """
    if mergeable == "CONFLICTING":
        return "update"
    if state in ("approve", "rerun"):
        return state  # getting CI to run never depends on mergeability
    if mergeable not in ("MERGEABLE", ""):
        return "wait"  # GitHub is still computing mergeability
    if state == "success":
        return "merge"
    if state == "failure" and behind:
        return "update"
    return "wait"


def train(repo: str, max_updates: int, resolver_cmd: str, dry_run: bool, token_is_bot: bool) -> int:
    prs = gh_json(
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--base",
        "main",
        "--limit",
        "100",
        "--json",
        "number,title,headRefName,headRefOid,isDraft,labels,baseRefName,isCrossRepository,mergeable",
    )
    queue = sorted(
        (p for p in prs or [] if eligible(p)),  # type: ignore[union-attr]
        key=lambda p: (not any(lb["name"] == "priority" for lb in p["labels"]), p["number"]),
    )
    print(f"[train] {len(queue)} eligible pull request(s)", flush=True)
    updates = 0
    merged = False
    summary: list[str] = []
    for pr in queue:
        number, branch, sha = pr["number"], pr["headRefName"], pr["headRefOid"]
        git("fetch", "--quiet", "origin", "main", branch)
        behind = git("merge-base", "--is-ancestor", "origin/main", sha, check=False).returncode != 0
        state, run_id = gate_state(repo, sha)
        action = decide(pr.get("mergeable") or "", state, behind)
        if action == "merge" and merged:
            action = "wait"  # one landing per tick; the next tick sees the new main
        if action == "update":
            if updates >= max_updates:
                summary.append(f"#{number}: needs an update, waits for a free slot")
                continue
            updates += 1
            try:
                outcome = update_pr(pr, resolver_cmd, dry_run, token_is_bot)
            except (subprocess.CalledProcessError, OSError) as exc:
                # One broken branch must never stop the train for the others.
                print(f"[train] #{number}: update crashed: {exc}", flush=True)
                outcome = f"update crashed ({type(exc).__name__}); left for the next tick"
            finally:
                git("merge", "--abort", check=False)
                git("rebase", "--abort", check=False)
                git("reset", "-q", "--hard", check=False)
                git("clean", "-fdq", "--", "jarvis/ui/web/dist", check=False)
            summary.append(f"#{number}: {outcome}")
        elif action == "merge":
            if dry_run:
                summary.append(f"#{number}: would merge")
                continue
            if gh("pr", "merge", str(number), "--repo", repo, "--squash") == 0:
                merged = True
                summary.append(f"#{number}: merged")
                if token_is_bot:
                    dispatch_ci("main")  # a GITHUB_TOKEN merge fires no push event
            else:
                summary.append(f"#{number}: merge refused (protection or conflict)")
        elif action in ("approve", "rerun"):
            if dry_run:
                summary.append(f"#{number}: would {action} CI run {run_id}")
                continue
            if action == "approve":
                code = gh("api", "-X", "POST", f"repos/{repo}/actions/runs/{run_id}/approve")
            else:
                code = gh("run", "rerun", str(run_id), "--repo", repo)
            if code == 0:
                summary.append(f"#{number}: CI run {run_id} {action}d")
            else:
                summary.append(
                    f"#{number}: could not {action} CI run {run_id} - approve it in the "
                    "Actions tab or set the INTEGRATION_TOKEN secret"
                )
        else:
            summary.append(f"#{number}: waiting (CI {state}, {pr.get('mergeable') or 'unknown'})")
    lines = ["## Merge train", "", *[f"- {line}" for line in summary]]
    print("\n".join(lines))
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


def update_pr(pr: dict, resolver_cmd: str, dry_run: bool, token_is_bot: bool) -> str:
    number, branch = pr["number"], pr["headRefName"]
    git("checkout", "--quiet", "-B", f"train/{number}", f"origin/{branch}")
    report = update("origin/main", "merge", resolver_cmd, REPO_ROOT)
    status = report["status"]
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if status == "conflict":
        files = "\n".join(f"- `{f}`" for f in report.get("unresolved", []))  # type: ignore[union-attr]
        body = (
            "The merge train could not bring this branch up to date with `main`: "
            f"these files conflict and could not be resolved automatically.\n\n{files}\n\n"
            "Resolve locally with `python scripts/agent_land.py --pr` (it rebases, "
            "resolves generated files and lists what is left). The train retries on "
            "the next push to this branch."
        )
        if not dry_run:
            gh("pr", "comment", str(number), "--repo", repo, "--body", body)
            gh("pr", "edit", str(number), "--repo", repo, "--add-label", "needs-rebase")
        return "conflict - " + ", ".join(report.get("unresolved", []))  # type: ignore[arg-type]
    if dry_run:
        return f"would push update ({status})"
    push = git("push", "origin", f"HEAD:refs/heads/{branch}", check=False)
    if push.returncode != 0:
        return "update rejected (the branch moved); retry next tick"
    gh("pr", "edit", str(number), "--repo", repo, "--remove-label", "needs-rebase")
    ai = report.get("ai_resolved") or []
    if ai:
        listing = ", ".join(f"`{f}`" for f in ai)  # type: ignore[union-attr]
        gh(
            "pr",
            "comment",
            str(number),
            "--repo",
            repo,
            "--body",
            f"Merge train: conflicts in {listing} were resolved by the configured AI "
            "resolver. CI re-runs on the result before anything lands; please skim them.",
        )
    # A GITHUB_TOKEN push parks the pull_request run in action_required; the
    # next tick approves it (a dispatched run would never reach the PR).
    return f"updated with main ({len(report.get('resolved', []))} conflicts auto-resolved)"  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update")
    up.add_argument("--onto", default="origin/main")
    up.add_argument("--mode", choices=("merge", "rebase"), default="rebase")
    up.add_argument("--resolver-cmd", default=os.environ.get("CONFLICT_RESOLVER_CMD", ""))
    tr = sub.add_parser("train")
    tr.add_argument("--repo", required=True)
    tr.add_argument("--max-updates", type=int, default=3)
    tr.add_argument("--resolver-cmd", default=os.environ.get("CONFLICT_RESOLVER_CMD", ""))
    tr.add_argument("--dry-run", action="store_true")
    tr.add_argument("--token-is-bot", action="store_true")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "update":
        report = update(args.onto, args.mode, args.resolver_cmd)
        print(json.dumps(report, indent=2))
        return 0 if report["status"] != "conflict" else 2
    return train(args.repo, args.max_updates, args.resolver_cmd, args.dry_run, args.token_is_bot)


if __name__ == "__main__":
    sys.exit(main())
