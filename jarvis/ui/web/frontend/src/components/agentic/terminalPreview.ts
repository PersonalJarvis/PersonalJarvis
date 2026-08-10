import { useSyncExternalStore } from "react";

/** A small, read-only view of the live xterm screen used by the Command Deck. */
export type TerminalPreview = readonly string[];

type Listener = () => void;
type PreviewSource = () => void;

const EMPTY_PREVIEW: TerminalPreview = Object.freeze([] as string[]);
const previews = new Map<string, TerminalPreview>();
const listeners = new Map<string, Set<Listener>>();
const sources = new Map<string, Set<PreviewSource>>();

function samePreview(left: TerminalPreview, right: TerminalPreview): boolean {
  return (
    left.length === right.length &&
    left.every((line, index) => line === right[index])
  );
}

function previewFor(name: string): TerminalPreview {
  return previews.get(name) ?? EMPTY_PREVIEW;
}

/** Publish an already parsed xterm viewport without putting output in React state. */
export function publishTerminalPreview(
  name: string,
  lines: TerminalPreview,
): void {
  const next = Object.freeze([...lines]);
  if (samePreview(previewFor(name), next)) return;
  previews.set(name, next);
  listeners.get(name)?.forEach((notify) => notify());
}

/** Remove a pane's stale screen when its real terminal is destroyed. */
export function clearTerminalPreview(name: string): void {
  if (!previews.delete(name)) return;
  listeners.get(name)?.forEach((notify) => notify());
}

/** True only while a deck card is asking to see this pane. */
export function terminalPreviewRequested(name: string): boolean {
  return (listeners.get(name)?.size ?? 0) > 0;
}

/**
 * Register the live xterm as the source for a card.
 *
 * A newly opened deck requests an immediate snapshot, so it does not wait for
 * the agent's next byte before showing a terminal that has already drawn.
 */
export function registerTerminalPreviewSource(
  name: string,
  requestSnapshot: PreviewSource,
): () => void {
  const registered = sources.get(name) ?? new Set<PreviewSource>();
  registered.add(requestSnapshot);
  sources.set(name, registered);
  if (terminalPreviewRequested(name)) requestSnapshot();
  return () => {
    registered.delete(requestSnapshot);
    if (registered.size === 0) sources.delete(name);
  };
}

export function subscribeTerminalPreview(
  name: string,
  listener: Listener,
): () => void {
  const registered = listeners.get(name) ?? new Set<Listener>();
  registered.add(listener);
  listeners.set(name, registered);
  sources.get(name)?.forEach((requestSnapshot) => requestSnapshot());
  return () => {
    registered.delete(listener);
    if (registered.size === 0) listeners.delete(name);
  };
}

/** Subscribe one card to its real terminal's latest visible rows. */
export function useTerminalPreview(name: string): TerminalPreview {
  return useSyncExternalStore(
    (listener) => subscribeTerminalPreview(name, listener),
    () => previewFor(name),
    () => EMPTY_PREVIEW,
  );
}

interface BufferLineLike {
  /** True when this physical xterm row continues the previous logical line. */
  isWrapped?: boolean;
  translateToString(
    trimRight?: boolean,
    startColumn?: number,
    endColumn?: number,
  ): string;
}

/**
 * Remove transport-shaped debris from the glance view only.
 *
 * The full xterm remains byte-for-byte faithful. Compact cards, however, are
 * not useful when a CLI has printed escaped control tokens as visible text
 * (the reported `Befud\\ndling` / `component\\ns` fragments). Joining those
 * tokens repairs the intended word without teaching the terminal itself to
 * rewrite user output.
 */
function cleanPreviewRow(value: string): string {
  return value
    .replace(/\\(?:x1b|u001b)\[[0-?]*[ -/]*[@-~]/gi, "")
    .replace(/\\[rn]/g, "")
    .replace(/\\t/g, "  ")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "")
    .trim();
}

interface TerminalBufferLike {
  type: string;
  baseY: number;
  getLine?(line: number): BufferLineLike | undefined;
}

interface TerminalLike {
  rows: number;
  buffer: { active: TerminalBufferLike };
}

/** Read the live screen, after xterm has resolved ANSI cursor movement and redraws. */
export function readTerminalPreview(
  term: TerminalLike,
  maxRows = 7,
): TerminalPreview {
  const buffer = term.buffer.active;
  if (!buffer.getLine || maxRows <= 0) return EMPTY_PREVIEW;
  const screenStart = buffer.type === "normal" ? buffer.baseY : 0;
  const rows: string[] = [];
  for (let row = 0; row < term.rows; row += 1) {
    const line = buffer.getLine(screenStart + row);
    const text = cleanPreviewRow(line?.translateToString(true) ?? "");
    if (line?.isWrapped && rows.length > 0) {
      rows[rows.length - 1] += text;
    } else {
      rows.push(text);
    }
  }
  let lastContent = rows.length - 1;
  while (lastContent >= 0 && rows[lastContent].trim().length === 0)
    lastContent -= 1;
  if (lastContent < 0) return EMPTY_PREVIEW;
  return rows.slice(Math.max(0, lastContent - maxRows + 1), lastContent + 1);
}
