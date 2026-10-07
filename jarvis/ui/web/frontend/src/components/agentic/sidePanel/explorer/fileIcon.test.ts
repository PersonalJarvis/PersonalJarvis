import { describe, expect, it } from "vitest";
import { FILE_ICON_COLORS, fileIconToken } from "./fileIcon";
import { FILE_ICON_SPRITE, FILE_ICON_TOKENS } from "./fileIconSprite";

describe("fileIconToken", () => {
  it.each([
    ["appshot-pr-body.md", "markdown"],
    ["appshot_voice_proof.py", "python"],
    ["appshot-recordings-gallery.png", "image"],
    ["voice-final-build.log", "text"],
    ["hooks.json", "json"],
    ["ThreadTimeline.tsx", "react"],
    ["threadWork.ts", "typescript"],
    ["pyproject.toml", "text"],
    ["run.bat", "bash"],
    ["preflight.ps1", "bash"],
    ["AGENTS.md", "agents"],
    ["package.json", "npm"],
    [".gitignore", "git"],
    [".env.local", "text"],
    ["demo.mp4", "video"],
    ["jarvis/ui/web/vite.config.ts", "vite"],
    ["config-drift-guard.log.1", "default"],
    ["Makefile", "default"],
  ])("%s -> %s", (name, token) => {
    expect(fileIconToken(name)).toBe(token);
  });

  it("has a symbol and a colour pair for every token", () => {
    for (const token of FILE_ICON_TOKENS) {
      expect(FILE_ICON_SPRITE).toContain(`id="jv-file-icon-${token}"`);
      expect(FILE_ICON_COLORS[token]).toHaveLength(2);
    }
  });
});
