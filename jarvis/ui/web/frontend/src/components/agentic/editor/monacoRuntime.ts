/**
 * The Monaco editor engine, set up for the desktop app.
 *
 * Reached only through the lazily imported editor surface, so the engine's
 * several megabytes never load on the app's boot path (AP-26) — only the first
 * time someone opens a file. Everything is bundled locally: no CDN, so the
 * editor works offline.
 *
 * Deliberately lean: the editor core with all its editing features, syntax
 * highlighting for every language Monaco ships, and the JSON language service.
 * The TypeScript, CSS and HTML language services would add about 9 MB of
 * workers for completions that the coding agents beside the editor already
 * provide.
 */
import * as monaco from "monaco-editor/editor/editor.api";
import "monaco-editor/features/register.all";
import "monaco-editor/languages/definitions/register.all";
import "monaco-editor/languages/features/json/register";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/languages/features/json/json.worker?worker";

import { setMonaco } from "./editorModels";

declare global {
  interface Window {
    MonacoEnvironment?: { getWorker: (workerId: string, label: string) => Worker };
  }
}

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    return label === "json" ? new JsonWorker() : new EditorWorker();
  },
};

setMonaco(monaco);

let probe: CanvasRenderingContext2D | null | undefined;

/** A theme token (`--background: 0 0% 100%`) as the `#rrggbb[aa]` Monaco needs. */
function tokenHex(name: string, fallback: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return fallback;
  if (probe === undefined) probe = document.createElement("canvas").getContext("2d");
  if (!probe) return fallback;
  // The canvas normalises any CSS colour; an unparseable one keeps the reset.
  probe.fillStyle = fallback;
  probe.fillStyle = /^[\d.]+(deg)?\s/.test(raw) ? `hsl(${raw})` : raw;
  const value = String(probe.fillStyle);
  if (value.startsWith("#")) return value;
  const parts = value.match(/[\d.]+/g)?.map(Number) ?? [];
  if (parts.length < 3) return fallback;
  const [red, green, blue, alpha = 1] = parts;
  return `#${[red, green, blue, Math.round(alpha * 255)].map((part) => part.toString(16).padStart(2, "0")).join("")}`;
}

/** Re-read the app's colour tokens into the editor theme (light or dark). */
export function applyTheme(dark: boolean): void {
  const background = tokenHex("--background", dark ? "#171717" : "#ffffff");
  const foreground = tokenHex("--foreground", dark ? "#e5e5e5" : "#171717");
  const muted = tokenHex("--muted-foreground", dark ? "#a3a3a3" : "#737373");
  const border = tokenHex("--border", dark ? "#2e2e2e" : "#e5e5e5");
  const lift = tokenHex("--secondary", dark ? "#262626" : "#f5f5f5");
  const popover = tokenHex("--popover", background);
  monaco.editor.defineTheme("jarvis", {
    base: dark ? "vs-dark" : "vs",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": background,
      "editor.foreground": foreground,
      "editorGutter.background": background,
      "minimap.background": background,
      "editorLineNumber.foreground": `${muted.slice(0, 7)}99`,
      "editorLineNumber.activeForeground": foreground,
      "editor.lineHighlightBackground": `${lift.slice(0, 7)}aa`,
      "editor.lineHighlightBorder": "#00000000",
      "editorIndentGuide.background1": border,
      "editorWidget.background": popover,
      "editorWidget.border": border,
      "editorSuggestWidget.background": popover,
      "editorSuggestWidget.border": border,
      "editorHoverWidget.background": popover,
      "editorHoverWidget.border": border,
      "input.background": lift,
      "input.border": border,
      "focusBorder": border,
      "scrollbar.shadow": "#00000000",
      "diffEditor.border": border,
    },
  });
  monaco.editor.setTheme("jarvis");
}

export const EDITOR_OPTIONS: monaco.editor.IEditorOptions & monaco.editor.IGlobalEditorOptions = {
  automaticLayout: true,
  fontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  fontSize: 13,
  lineHeight: 20,
  minimap: { enabled: true, renderCharacters: false, maxColumn: 100 },
  stickyScroll: { enabled: true },
  bracketPairColorization: { enabled: true },
  guides: { bracketPairs: "active", indentation: true },
  smoothScrolling: true,
  cursorSmoothCaretAnimation: "on",
  renderWhitespace: "selection",
  scrollBeyondLastLine: false,
  padding: { top: 8, bottom: 8 },
  // Hovers and the suggest list may overflow the editor's box instead of
  // being clipped by the stage around it.
  fixedOverflowWidgets: true,
  // The app has no browser zoom, and Ctrl+wheel belongs to the app zoom.
  mouseWheelZoom: false,
  // The find widget floats over the text instead of pushing it down.
  find: { addExtraSpaceOnTop: false, seedSearchStringFromSelection: "selection" },
};

export { monaco };
