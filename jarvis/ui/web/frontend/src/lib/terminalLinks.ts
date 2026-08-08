/** Safe URL and workspace-path links shared by the embedded xterm terminals. */
import type {
  IDisposable,
  ILink,
  ILinkHandler,
  ILinkProvider,
  ITerminalAddon,
  Terminal,
} from "@xterm/xterm";

import { openTerminalTarget } from "@/lib/agenticIdeApi";
import { openExternalUrl } from "@/lib/openExternal";

const MAX_LOGICAL_LINE_CHARS = 4096;

const BARE_FILE = /^(?:(?:readme|license|makefile|dockerfile|containerfile)|[^/\\]+\.(?:c|cc|cpp|h|hpp|cs|go|rs|java|kt|kts|swift|php|rb|py|pyi|js|mjs|cjs|jsx|ts|mts|cts|tsx|vue|svelte|html?|css|scss|sass|less|json|jsonl|ya?ml|toml|xml|md|mdx|txt|log|csv|tsv|sql|sh|bash|zsh|fish|ps1|bat|cmd|ini|cfg|conf|env|lock|diff|patch|pdf|png|jpe?g|gif|svg|webp))(?::[1-9]\d*(?::[1-9]\d*)?|#L[1-9]\d*(?::[1-9]\d*)?|\([1-9]\d*(?:,[1-9]\d*)?\))?$/i;

const LOCATION_SUFFIX = /(?:#[lL][1-9]\d*(?::[1-9]\d*)?|:[1-9]\d*(?::[1-9]\d*)?|\([1-9]\d*(?:,[1-9]\d*)?\))$/;

export interface TerminalLinkOptions {
  /** Required for local paths; URLs do not need workspace context. */
  workspaceId?: string;
  onError?: (message: string) => void;
}

export interface TerminalPathMatch {
  /** Exact text painted by xterm, including a useful line/column suffix. */
  text: string;
  /** UTF-16 string offsets in the logical (possibly wrapped) terminal line. */
  start: number;
  end: number;
}

function isHttpUrl(value: string): boolean {
  try {
    const protocol = new URL(value).protocol;
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

/** Is this text plausibly a local path, without touching the filesystem? */
export function isTerminalPath(value: string): boolean {
  const target = value.trim().replace(LOCATION_SUFFIX, "");
  if (!target) return false;
  if (/^file:\/\//i.test(target)) return true;
  // Other URI schemes are handled nowhere in this module. In particular, an
  // OSC-8 javascript:/custom-app URI may never become a native launch.
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(target)) return false;
  if (/^[a-z]:[\\/]/i.test(target)) return true;
  if (/^\\\\[^\\]+\\[^\\]+/.test(target)) return true;
  if (/^\/(?!\/).+/.test(target)) return true;
  if (/^(?:\.{1,2}|~)[\\/]/.test(target)) return true;
  if (/^[a-z0-9_@.+-][^:\s]*[\\/].*/i.test(target)) return true;
  return BARE_FILE.test(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Could not open that terminal path.";
}

/**
 * Activate a terminal URL/path only on the conventional Ctrl/Cmd primary click.
 *
 * Ordinary clicks stay available for terminal selection. Paths additionally
 * require a workspace id; the backend resolves and confines them there before
 * it invokes the OS opener.
 */
export function activateTerminalLink(
  event: MouseEvent,
  uri: string,
  options: TerminalLinkOptions = {},
): void {
  if (event.button !== 0 || (!event.ctrlKey && !event.metaKey)) return;
  if (isHttpUrl(uri)) {
    void openExternalUrl(uri);
    return;
  }
  if (!options.workspaceId || !isTerminalPath(uri)) return;
  void openTerminalTarget(options.workspaceId, uri).catch((error: unknown) => {
    const message = errorMessage(error);
    if (options.onError) options.onError(message);
    else console.warn("Could not open terminal path:", message);
  });
}

export function createTerminalLinkActivator(
  options: TerminalLinkOptions,
): (event: MouseEvent, uri: string) => void {
  return (event, uri) => activateTerminalLink(event, uri, options);
}

export function createTerminalOscLinkHandler(
  options: TerminalLinkOptions = {},
): ILinkHandler {
  return {
    activate: createTerminalLinkActivator(options),
    // xterm drops file: links before activate unless this is explicit. The
    // activator above still rejects every non-http/non-path value.
    allowNonHttpProtocols: Boolean(options.workspaceId),
  };
}

/** Handler for hyperlinks emitted through the terminal's OSC-8 protocol. */
export const TERMINAL_OSC_LINK_HANDLER = createTerminalOscLinkHandler();

function closingDelimiterIsExtra(value: string, closer: string): boolean {
  const opener = closer === ")" ? "(" : closer === "]" ? "[" : "{";
  return value.split(closer).length > value.split(opener).length;
}

function trimToken(
  token: string,
  offset: number,
): TerminalPathMatch | undefined {
  let start = 0;
  let end = token.length;
  while (start < end && "([{<\"'`".includes(token[start])) start += 1;
  while (end > start) {
    const last = token[end - 1];
    const candidate = token.slice(start, end);
    if (".,;!?\"'`>".includes(last)) {
      end -= 1;
      continue;
    }
    if (")]}".includes(last) && closingDelimiterIsExtra(candidate, last)) {
      end -= 1;
      continue;
    }
    break;
  }
  const text = token.slice(start, end);
  if (!text || text.includes("](") || !isTerminalPath(text)) return undefined;
  return { text, start: offset + start, end: offset + end };
}

/** Find file/folder candidates in one ANSI-free logical terminal line. */
export function findTerminalPathMatches(line: string): TerminalPathMatch[] {
  const matches: TerminalPathMatch[] = [];
  const quotedRanges: Array<[number, number]> = [];
  const add = (token: string, offset: number) => {
    const found = trimToken(token, offset);
    if (!found || found.text.length > MAX_LOGICAL_LINE_CHARS) return;
    if (
      matches.some(
        (item) => item.start === found.start && item.end === found.end,
      )
    ) {
      return;
    }
    matches.push(found);
  };

  // Quoting is how shells make paths with spaces unambiguous. Keep the quote
  // itself out of the clickable range and send only the path to the backend.
  for (const match of line.matchAll(/(["'`])([^"'`\r\n]+)\1/g)) {
    if (match.index === undefined) continue;
    const start = match.index + 1;
    quotedRanges.push([match.index, match.index + match[0].length]);
    add(match[2], start);
  }

  for (const match of line.matchAll(/\S+/g)) {
    if (match.index === undefined) continue;
    const end = match.index + match[0].length;
    if (quotedRanges.some(([from, to]) => match.index! < to && end > from)) {
      continue;
    }
    add(match[0], match.index);
  }
  return matches.sort((left, right) => left.start - right.start);
}

interface LogicalLine {
  text: string;
  /** Zero-based row in xterm's active buffer. */
  startRow: number;
}

function logicalLineAt(
  terminal: Terminal,
  bufferLineNumber: number,
): LogicalLine | undefined {
  const buffer = terminal.buffer.active;
  let startRow = bufferLineNumber - 1;
  let line = buffer.getLine(startRow);
  if (!line) return undefined;

  let scannedBackward = 0;
  while (
    line.isWrapped &&
    startRow > 0 &&
    scannedBackward < MAX_LOGICAL_LINE_CHARS
  ) {
    const previous = buffer.getLine(startRow - 1);
    if (!previous) break;
    startRow -= 1;
    line = previous;
    scannedBackward += line.translateToString(true).length;
  }

  const chunks: string[] = [];
  let row = startRow;
  let measured = 0;
  while (measured < MAX_LOGICAL_LINE_CHARS) {
    const current = buffer.getLine(row);
    if (!current) break;
    const text = current.translateToString(true);
    chunks.push(text);
    measured += text.length;
    const next = buffer.getLine(row + 1);
    if (!next?.isWrapped) break;
    row += 1;
  }
  return { text: chunks.join(""), startRow };
}

/** Map a UTF-16 string offset back to xterm's row/cell coordinates. */
function mapStringIndex(
  terminal: Terminal,
  initialRow: number,
  initialColumn: number,
  chars: number,
): [number, number] | undefined {
  const buffer = terminal.buffer.active;
  const cell = buffer.getNullCell();
  let row = initialRow;
  let column = initialColumn;
  let remaining = chars;
  while (remaining > 0) {
    const line = buffer.getLine(row);
    if (!line) return undefined;
    for (let index = column; index < line.length; index += 1) {
      const current = line.getCell(index, cell);
      if (!current) continue;
      const value = current.getChars();
      if (current.getWidth() > 0) {
        remaining -= value.length || 1;
        if (index === line.length - 1 && value === "") {
          const next = buffer.getLine(row + 1);
          if (next?.isWrapped && next.getCell(0, cell)?.getWidth() === 2) {
            remaining += 1;
          }
        }
      }
      if (remaining < 0) return [row, index];
    }
    row += 1;
    column = 0;
  }
  return [row, column];
}

class TerminalPathLinkProvider implements ILinkProvider {
  constructor(
    private readonly terminal: Terminal,
    private readonly handler: (event: MouseEvent, uri: string) => void,
  ) {}

  provideLinks(
    bufferLineNumber: number,
    callback: (links: ILink[] | undefined) => void,
  ): void {
    const logical = logicalLineAt(this.terminal, bufferLineNumber);
    if (!logical) {
      callback(undefined);
      return;
    }
    const links = findTerminalPathMatches(logical.text).flatMap((match) => {
      const start = mapStringIndex(
        this.terminal,
        logical.startRow,
        0,
        match.start,
      );
      if (!start) return [];
      const end = mapStringIndex(
        this.terminal,
        start[0],
        start[1],
        match.text.length,
      );
      if (!end) return [];
      return [
        {
          text: match.text,
          range: {
            start: { x: start[1] + 1, y: start[0] + 1 },
            end: { x: end[1], y: end[0] + 1 },
          },
          activate: this.handler,
        },
      ];
    });
    callback(links.length ? links : undefined);
  }
}

/** xterm addon that makes printed workspace paths hoverable and activatable. */
export class TerminalPathLinksAddon implements ITerminalAddon {
  private registration?: IDisposable;

  constructor(
    private readonly handler: (event: MouseEvent, uri: string) => void,
  ) {}

  activate(terminal: Terminal): void {
    this.registration?.dispose();
    this.registration = terminal.registerLinkProvider(
      new TerminalPathLinkProvider(terminal, this.handler),
    );
  }

  dispose(): void {
    this.registration?.dispose();
    this.registration = undefined;
  }
}
