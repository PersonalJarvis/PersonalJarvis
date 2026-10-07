import {
  Eye,
  FilePenLine,
  Hand,
  NotebookPen,
  Shield,
  ShieldAlert,
  ShieldBan,
  WandSparkles,
} from "lucide-react";
import { describe, expect, it } from "vitest";

import { isUnguardedPermissionMode, permissionModeIcon } from "./permissionIcons";

describe("permissionModeIcon", () => {
  it("maps every vendor spelling of a stance onto the stance's one glyph", () => {
    // Ask before acting — the unified ladder, Claude Code, and agy.
    expect(permissionModeIcon("ask")).toBe(Hand);
    expect(permissionModeIcon("default")).toBe(Hand);
    expect(permissionModeIcon("approve-for-me")).toBe(Hand);
    // Edits go through.
    expect(permissionModeIcon("accept-edits")).toBe(FilePenLine);
    expect(permissionModeIcon("acceptEdits")).toBe(FilePenLine);
    // Nothing asks.
    expect(permissionModeIcon("bypass")).toBe(ShieldAlert);
    expect(permissionModeIcon("bypassPermissions")).toBe(ShieldAlert);
    expect(permissionModeIcon("full-access")).toBe(ShieldAlert);
    expect(permissionModeIcon("skip-permissions")).toBe(ShieldAlert);
    // The reading stances and the runner's own judgement.
    expect(permissionModeIcon("plan")).toBe(NotebookPen);
    expect(permissionModeIcon("read-only")).toBe(Eye);
    expect(permissionModeIcon("auto")).toBe(WandSparkles);
    expect(permissionModeIcon("dontAsk")).toBe(ShieldBan);
  });

  it("gives an id it has never seen a plain shield, never nothing", () => {
    expect(permissionModeIcon("something-new")).toBe(Shield);
    expect(permissionModeIcon("")).toBe(Shield);
  });

  it("keeps the four stances of the unified ladder visually distinct", () => {
    const glyphs = ["ask", "accept-edits", "plan", "bypass"].map(permissionModeIcon);
    expect(new Set(glyphs).size).toBe(glyphs.length);
  });

  it("flags only the stances in which nothing asks", () => {
    expect(["bypass", "bypassPermissions", "full-access", "skip-permissions"].every(isUnguardedPermissionMode)).toBe(true);
    expect(["ask", "default", "acceptEdits", "plan", "auto", "dontAsk"].some(isUnguardedPermissionMode)).toBe(false);
  });
});
