import {
  Eye,
  FilePen,
  NotebookPen,
  ShieldCheck,
  ShieldOff,
  ShieldQuestion,
  ShieldX,
  Sparkles,
} from "lucide-react";
import { describe, expect, it } from "vitest";

import { permissionModeIcon } from "./permissionIcons";

describe("permissionModeIcon", () => {
  it("maps every vendor spelling of a stance onto the stance's one glyph", () => {
    // Ask before acting — the unified ladder, Claude Code, and agy.
    expect(permissionModeIcon("ask")).toBe(ShieldQuestion);
    expect(permissionModeIcon("default")).toBe(ShieldQuestion);
    expect(permissionModeIcon("approve-for-me")).toBe(ShieldQuestion);
    // Edits go through.
    expect(permissionModeIcon("accept-edits")).toBe(FilePen);
    expect(permissionModeIcon("acceptEdits")).toBe(FilePen);
    // Nothing asks.
    expect(permissionModeIcon("bypass")).toBe(ShieldOff);
    expect(permissionModeIcon("bypassPermissions")).toBe(ShieldOff);
    expect(permissionModeIcon("full-access")).toBe(ShieldOff);
    expect(permissionModeIcon("skip-permissions")).toBe(ShieldOff);
    // The reading stances and the runner's own judgement.
    expect(permissionModeIcon("plan")).toBe(NotebookPen);
    expect(permissionModeIcon("read-only")).toBe(Eye);
    expect(permissionModeIcon("auto")).toBe(Sparkles);
    expect(permissionModeIcon("dontAsk")).toBe(ShieldX);
  });

  it("gives an id it has never seen the column's shield, never nothing", () => {
    expect(permissionModeIcon("something-new")).toBe(ShieldCheck);
    expect(permissionModeIcon("")).toBe(ShieldCheck);
  });

  it("keeps the four stances of the unified ladder visually distinct", () => {
    const glyphs = ["ask", "accept-edits", "plan", "bypass"].map(permissionModeIcon);
    expect(new Set(glyphs).size).toBe(glyphs.length);
  });
});
