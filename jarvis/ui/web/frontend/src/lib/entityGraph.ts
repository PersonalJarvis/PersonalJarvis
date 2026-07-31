/**
 * Visual encoding for the UltraWiki Explore view.
 *
 * Two properties of a topic carry meaning and both come from the data:
 * how OFTEN it comes up (node size, mention count) and WHEN it lived in your
 * history (the time bar in the list, the brightness in the graph). Nothing
 * here is decoration — a reader can point at any pixel and say what it means.
 *
 * Kept out of the components so the encoding is unit-testable without a
 * canvas, and so there is exactly one definition of what "recent" looks like.
 */

/** The time-bearing slice of an entity the encoding needs. */
export interface SpannedEntity {
  first_seen: string;
  last_seen: string;
  mentions: number;
}

/** Epoch-millisecond bounds of the whole corpus. */
export interface CorpusSpan {
  start: number;
  end: number;
}

/** Position of a topic's bar inside the corpus span, both in 0..1. */
export interface SpanBar {
  offset: number;
  width: number;
}

/** A single-moment topic still needs to be visible, so bars have a floor. */
const MIN_BAR_WIDTH = 0.035;

/** Node radius range in graph units. */
const MIN_RADIUS = 3;
const RADIUS_RANGE = 9;

/**
 * Oldest → newest tint: ash that warms into the signal yellow the whole app
 * is built on. ONE hue travelling from desaturated to saturated, deliberately
 * — two hues would read as two CATEGORIES, and recency is one continuous
 * axis. The dim end stays light enough to hold ~3.5:1 against the matte-black
 * background, so an old topic is quiet, never invisible.
 */
const TINT_OLD = { r: 0x6b, g: 0x66, b: 0x54 };
const TINT_NEW = { r: 0xff, g: 0xd6, b: 0x0a };

function parse(timestamp: string): number | null {
  const value = Date.parse(timestamp);
  return Number.isFinite(value) ? value : null;
}

/** Earliest first_seen and latest last_seen across every topic. */
export function corpusSpan(entities: readonly SpannedEntity[]): CorpusSpan {
  let start = Number.POSITIVE_INFINITY;
  let end = Number.NEGATIVE_INFINITY;
  for (const entity of entities) {
    const first = parse(entity.first_seen);
    const last = parse(entity.last_seen);
    if (first !== null) start = Math.min(start, first);
    if (last !== null) end = Math.max(end, last);
  }
  if (!Number.isFinite(start) || !Number.isFinite(end)) return { start: 0, end: 0 };
  return { start, end };
}

/**
 * Where a topic's life sits inside the corpus, as offset + width in 0..1.
 *
 * A corpus with no measurable span (one moment, or unparseable dates) gives
 * every topic the full width: claiming a position inside a span that does not
 * exist would be inventing information.
 */
export function spanBar(entity: SpannedEntity, span: CorpusSpan): SpanBar {
  const total = span.end - span.start;
  if (total <= 0) return { offset: 0, width: 1 };

  const first = parse(entity.first_seen);
  const last = parse(entity.last_seen);
  if (first === null || last === null) return { offset: 0, width: 1 };

  const offset = Math.min(Math.max((first - span.start) / total, 0), 1);
  const raw = Math.max((last - first) / total, 0);
  const width = Math.min(Math.max(raw, MIN_BAR_WIDTH), 1 - offset || MIN_BAR_WIDTH);
  return { offset: Math.min(offset, 1 - width), width };
}

/**
 * Node radius for a mention count.
 *
 * Square-root rather than linear: on a real corpus the top topic is mentioned
 * a hundred times more than the tail, and a linear scale turns that into one
 * blob with dust around it. The root compresses the range while keeping the
 * order intact and readable.
 */
export function nodeRadius(mentions: number, maxMentions: number): number {
  const ceiling = Math.max(maxMentions, 1);
  const share = Math.min(Math.max(mentions, 0) / ceiling, 1);
  return MIN_RADIUS + RADIUS_RANGE * Math.sqrt(share);
}

function hex(channel: number): string {
  return Math.round(Math.min(Math.max(channel, 0), 255))
    .toString(16)
    .padStart(2, "0");
}

/**
 * Colour for "last mentioned at this point in the span" — dim for long ago,
 * bright for recent. An unreadable timestamp lands at the dim end rather than
 * producing a NaN colour the canvas would silently drop.
 */
export function recencyTint(lastSeen: string, span: CorpusSpan): string {
  const total = span.end - span.start;
  const last = parse(lastSeen);
  const position =
    total > 0 && last !== null
      ? Math.min(Math.max((last - span.start) / total, 0), 1)
      : 0;
  const r = TINT_OLD.r + (TINT_NEW.r - TINT_OLD.r) * position;
  const g = TINT_OLD.g + (TINT_NEW.g - TINT_OLD.g) * position;
  const b = TINT_OLD.b + (TINT_NEW.b - TINT_OLD.b) * position;
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}
