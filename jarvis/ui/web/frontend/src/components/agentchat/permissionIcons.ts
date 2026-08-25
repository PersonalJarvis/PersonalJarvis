import {
  Eye,
  FilePen,
  NotebookPen,
  ShieldCheck,
  ShieldOff,
  ShieldQuestion,
  ShieldX,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

/**
 * One glyph per permission STANCE, not per vendor spelling.
 *
 * The composer's permission pill draws whatever ladder the catalog delivers
 * (jarvis/agent_chat/permissions.py): the unified ladder on the front page
 * (`ask` / `accept-edits` / `plan` / `bypass`) and, for an IDE session, a
 * runner's own ids (`default`, `acceptEdits`, `bypassPermissions`, `auto`,
 * `read-only`, `full-access`, …). Several of those mean the same thing, so
 * they wear the same glyph — a person who learned "the pen means edits go
 * through" recognises it on every provider. Anything unknown falls back to
 * the column's shield so a new ladder entry is never blank.
 */
const GLYPHS: Record<string, LucideIcon> = {
  // Ask before acting.
  ask: ShieldQuestion,
  default: ShieldQuestion,
  "approve-for-me": ShieldQuestion,
  // Edits go through, everything else asks.
  "accept-edits": FilePen,
  acceptEdits: FilePen,
  // Nothing asks.
  bypass: ShieldOff,
  bypassPermissions: ShieldOff,
  "full-access": ShieldOff,
  "skip-permissions": ShieldOff,
  // Reads and plans, changes nothing.
  plan: NotebookPen,
  "read-only": Eye,
  // The runner decides on its own.
  auto: Sparkles,
  // Never asks — and refuses what it would have asked about.
  dontAsk: ShieldX,
};

export function permissionModeIcon(id: string): LucideIcon {
  return GLYPHS[id] ?? ShieldCheck;
}
