"""Standalone-CLI fuer Skill-Management.

Aufruf (ohne __main__.py-Touch, um Merge-Konflikte mit Phase 1b zu vermeiden):

    python -m jarvis.skills.cli --list
    python -m jarvis.skills.cli --info morning-routine
    python -m jarvis.skills.cli --run deep-work-mode
    python -m jarvis.skills.cli --import-claude-skills ~/.claude/skills/

Die CLI laedt Skills aus:
  1. user_skills_dir()                       (User-Skills; Windows: %LOCALAPPDATA%\Jarvis\skills)
  2. <project>/jarvis/skills/builtin/        (mitgelieferte Skills)

Beide Roots werden rekursiv durchsucht. Der Aufruf geht gracefully mit
fehlenden B1/B2-Komponenten um — wenn `registry.py` oder MCP-Adapter
noch nicht existieren, zeigen wir was wir haben und weisen auf fehlende
Abhaengigkeiten hin, crashen aber nicht.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Windows: stdout auf UTF-8, damit Umlaute und Unicode-Symbole funktionieren.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------

def _skill_roots() -> list[Path]:
    """Liste der Skill-Root-Verzeichnisse in Such-Reihenfolge."""
    try:
        from jarvis.core.config import PROJECT_ROOT
    except Exception:  # noqa: BLE001
        PROJECT_ROOT = Path(__file__).resolve().parents[2]

    from jarvis.core.paths import user_skills_dir

    roots = [
        user_skills_dir(),
        PROJECT_ROOT / "jarvis" / "skills" / "builtin",
    ]
    return [r for r in roots if r.exists()]


def _collect_skills() -> list:
    """Laed alle Skills aus allen Roots. Robust gegen fehlendes registry-Modul."""
    from jarvis.skills.loader import discover_skills

    skills = []
    for root in _skill_roots():
        try:
            skills.extend(discover_skills(root))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] Konnte Skills aus {root} nicht laden: {e}", file=sys.stderr)
    return skills


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------

def _list_skills() -> int:
    from jarvis.core.paths import user_skills_dir

    skills = _collect_skills()
    if not skills:
        print("Keine Skills gefunden.")
        print("")
        print("Erwartete Pfade:")
        for r in [user_skills_dir(),
                  Path(__file__).resolve().parents[2] / "jarvis" / "skills" / "builtin"]:
            print(f"  - {r}")
        return 0

    print(f"{'Name':<24} {'Version':<10} {'State':<12} {'Triggers'}")
    print("-" * 80)
    for s in skills:
        fm = s.frontmatter
        if fm is None:
            name = s.path.parent.name
            print(f"{name:<24} {'?':<10} {'DRAFT':<12} (parse error: {s.error or '?'})")
            continue
        trig_summary = ",".join(t.type for t in fm.triggers) or "-"
        print(
            f"{fm.name:<24} {fm.version:<10} "
            f"{s.state.value:<12} {trig_summary}"
        )
    return 0


def _skill_info(name: str) -> int:
    skills = _collect_skills()
    match = next(
        (s for s in skills if (s.frontmatter and s.frontmatter.name == name)
         or s.path.parent.name == name),
        None,
    )
    if match is None:
        print(f"Skill '{name}' nicht gefunden.")
        return 2

    fm = match.frontmatter
    print(f"Pfad:        {match.path}")
    print(f"State:       {match.state.value}")
    if match.error:
        print(f"Error:       {match.error}")
    if fm is None:
        return 0

    print(f"Name:        {fm.name}")
    print(f"Version:     {fm.version}")
    print(f"Category:    {fm.category}")
    print(f"Tags:        {', '.join(fm.tags) or '-'}")
    print(f"Author:      {fm.author or '-'}")
    print(f"License:     {fm.license}")
    print(f"Description: {fm.description.strip()}")
    print(f"Token-Est:   {fm.token_budget_estimate}")
    print("Triggers:")
    for t in fm.triggers:
        details = (
            f"pattern={t.pattern!r}" if t.type == "voice"
            else f"combo={t.combo!r}" if t.type == "hotkey"
            else f"cron={t.cron!r}"
        )
        print(f"  - {t.type}: {details} lang={t.language}")
    print("Requires-Tools:")
    for tool in fm.requires_tools:
        override = fm.risk_policy.per_tool_overrides.get(tool)
        suffix = f" (tier={override})" if override else ""
        print(f"  - {tool}{suffix}")
    print(f"Risk default-tier: {fm.risk_policy.default_tier}")
    if fm.config:
        print("Config:")
        for k, v in fm.config.items():
            print(f"  {k}: {v!r}")
    return 0


async def _skill_run(name: str) -> int:
    skills = _collect_skills()
    match = next(
        (s for s in skills if (s.frontmatter and s.frontmatter.name == name)),
        None,
    )
    if match is None:
        print(f"Skill '{name}' nicht gefunden oder nicht validiert.")
        return 2

    # Vollstaendiges Run benoetigt Tool-Registry + MCP — beides wird
    # parallel von B1/B2 gebaut. Wir machen best-effort.
    try:
        from jarvis.core.bus import EventBus
        from jarvis.skills.runner import SkillRunner  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[info] SkillRunner nicht verfuegbar: {e}")
        print("[info] Ausfuehrung benoetigt Phase-1c-B1 + B2 (Tool-Registry + MCP).")
        print(f"[info] Dry-Run: Skill '{name}' wuerde laufen mit Triggern "
              f"{[t.type for t in match.frontmatter.triggers]}")
        return 0

    bus = EventBus()
    try:
        # SkillRegistry ist mandatory (kein Default). Wir nutzen die gleiche
        # Discovery-Logik wie _list_skills(): alle Roots kombinieren.
        from jarvis.core.paths import user_skills_dir
        from jarvis.skills.registry import SkillRegistry

        roots = [
            user_skills_dir(),
            Path(__file__).resolve().parents[1] / "skills" / "builtin",
        ]
        combined_registry = None
        for root in roots:
            if root.exists():
                reg = SkillRegistry(root, bus)
                reg.reload()
                combined_registry = reg  # letzter gewinnt, fallback-Pattern
                if reg.get(name) is not None:
                    break
        runner = SkillRunner(registry=combined_registry, bus=bus)  # type: ignore[call-arg]
        result = await runner.run(match)  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        print(f"[error] Skill-Run fehlgeschlagen: {e}")
        return 1

    print(f"Skill: {result.skill_name}")
    print(f"Success: {result.success}")
    print(f"Duration: {result.duration_ms}ms")
    if result.error:
        print(f"Error: {result.error}")
    return 0 if result.success else 1


def _import_claude_skills(src: str) -> int:
    """Liest OpenClaw-Skills (.md), ergaenzt fehlende Frontmatter-Defaults,
    kopiert nach ``user_skills_dir()/<name>/SKILL.md``.
    """
    from jarvis.core.paths import user_skills_dir

    src_dir = Path(src).expanduser()
    if not src_dir.exists():
        print(f"Quell-Verzeichnis existiert nicht: {src_dir}")
        return 2

    dst_dir = user_skills_dir()
    dst_dir.mkdir(parents=True, exist_ok=True)

    imported = 0
    skipped = 0
    for md in src_dir.rglob("*.md"):
        rel = md.relative_to(src_dir)
        # Name = Parent-Dir oder Dateistem
        name = md.parent.name if md.parent != src_dir else md.stem
        name = name.replace(" ", "-").lower()
        out = dst_dir / name / "SKILL.md"
        out.parent.mkdir(parents=True, exist_ok=True)

        text = md.read_text(encoding="utf-8")
        enriched = _ensure_frontmatter(text, name)
        if out.exists():
            skipped += 1
            continue
        out.write_text(enriched, encoding="utf-8")
        imported += 1
        print(f"  imported: {rel} -> {out}")

    print(f"Imported {imported} OpenClaw-Skills (skipped {skipped} existing).")
    print(f"Ziel: {dst_dir}")
    return 0


def _ensure_frontmatter(text: str, default_name: str) -> str:
    """Prueft ob `text` bereits YAML-Frontmatter hat. Falls nicht, prepend default."""
    stripped = text.lstrip()
    if stripped.startswith("---"):
        return text

    default = (
        "---\n"
        'schema_version: "1"\n'
        f"name: {default_name}\n"
        'version: "0.1.0"\n'
        "description: Imported from OpenClaw skills.\n"
        "category: imported\n"
        "author: openclaw-import\n"
        "triggers: []\n"
        "requires_tools: []\n"
        "risk_policy:\n"
        "  default_tier: monitor\n"
        "---\n\n"
    )
    return default + text


# ----------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------

def _list_drafts() -> int:
    """Phase 7.5: tabellarische Übersicht aller DRAFT-Skills."""
    from jarvis.core.paths import user_skills_dir
    from jarvis.skills.registry import SkillRegistry

    root = user_skills_dir()
    if not root.exists():
        print("Kein user_skills_dir vorhanden — keine Drafts.")
        return 0
    registry = SkillRegistry(root, bus=None)
    registry.reload_sync()
    drafts = registry.list_drafts()
    if not drafts:
        print("Keine Draft-Skills vorhanden.")
        return 0
    print(f"{'SLUG':<32} {'STATE':<10} {'DESCRIPTION'}")
    for skill in sorted(drafts, key=lambda s: s.path.parent.name):
        slug = skill.path.parent.name
        desc = skill.frontmatter.description if skill.frontmatter else (skill.error or "")
        desc = (desc or "")[:60]
        print(f"{slug:<32} {skill.state.value:<10} {desc}")
    return 0


def _promote_skill(slug: str) -> int:
    """Phase 7.5: setzt einen DRAFT-Skill auf state=active.

    Plan-§AP-6: User-explizite Aktivierung — der Skill triggert erst
    nach diesem Schritt. Der Sicherheits-Lint im Registry prüft den
    Body vor dem Promote.
    """
    from jarvis.core.paths import user_skills_dir
    from jarvis.skills.authoring.draft_writer import UnsafeSkillError
    from jarvis.skills.registry import SkillRegistry

    root = user_skills_dir()
    if not root.exists():
        print(f"[error] user_skills_dir existiert nicht: {root}", file=sys.stderr)
        return 1
    registry = SkillRegistry(root, bus=None)
    registry.reload_sync()
    try:
        skill = registry.promote(slug)
    except KeyError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except UnsafeSkillError as exc:
        print(f"[error] Promote blockiert (unsafe code): {exc}", file=sys.stderr)
        return 1
    print(f"Skill '{slug}' ist jetzt aktiv (state={skill.state.value}).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jarvis-skills")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Alle Skills auflisten")
    group.add_argument("--info", type=str, metavar="NAME", help="Details zu einem Skill")
    group.add_argument("--run", type=str, metavar="NAME", help="Skill ausfuehren (requires MCP)")
    group.add_argument(
        "--import-claude-skills",
        type=str,
        metavar="PATH",
        dest="import_claude_skills",
        help="OpenClaw-Skills aus einem Verzeichnis importieren",
    )
    # Phase 7.5: Draft-Skill-Management (Plan-§7.5 Voice-Output + UI in Phase 7.6).
    group.add_argument(
        "--list-drafts",
        action="store_true",
        dest="list_drafts",
        help="Alle OpenClaw-authored Drafts auflisten (state=draft).",
    )
    group.add_argument(
        "--promote",
        type=str,
        metavar="SLUG",
        help="Einen Draft-Skill auf state=active promoten.",
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.list:
        return _list_skills()
    if args.info:
        return _skill_info(args.info)
    if args.run:
        return asyncio.run(_skill_run(args.run))
    if args.import_claude_skills:
        return _import_claude_skills(args.import_claude_skills)
    if args.list_drafts:
        return _list_drafts()
    if args.promote:
        return _promote_skill(args.promote)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
