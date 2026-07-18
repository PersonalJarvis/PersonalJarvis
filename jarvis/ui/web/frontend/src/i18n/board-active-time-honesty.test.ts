/**
 * Honesty guard for the Board "active time" metric.
 *
 * The number behind this metric is the total wall-clock time voice sessions
 * were OPEN (wake -> hang-up) — summed from voice_sessions.(ended_ms - started_ms)
 * in jarvis/board/aggregator.py::_aggregate_sessions. It is NOT the time anyone
 * actually spoke: it includes every silence, every pause while the user formulates
 * a command, and all STT -> LLM -> TTS latency.
 *
 * It used to be mislabeled "Talk time" / "{0} h talked" / "Conversation hours",
 * which over-claimed real speech (e.g. 27 h "talk time" implied an impossible
 * ~18 words/minute). These assertions lock the user-facing labels to an honest
 * "active" framing across all three locales so the false speech wording cannot
 * silently regress.
 */
import { describe, expect, it } from "vitest";

import en from "./locales/en.json";
import de from "./locales/de.json";
import es from "./locales/es.json";

type Board = {
  hero: { talk_time: string };
  share: { card: { talk_line: string } };
  heatmap_tooltip: string;
  stats: { conversation_hours: string; hours_saved: string };
  records: {
    most_conversation_hours_in_a_day: { title: string };
    most_hours_saved_in_a_day: { title: string };
  };
};

/** The five surfaces that all render the same session-open-time number. */
function sessionTimeLabels(board: Board): string[] {
  return [
    board.hero.talk_time,
    board.share.card.talk_line,
    board.heatmap_tooltip,
    board.stats.conversation_hours,
    board.records.most_conversation_hours_in_a_day.title,
  ];
}

// Per-locale wording that would (falsely) claim the metric measures speech.
// Note: the bare count nouns "conversations" / "Gespräche" / "conversaciones"
// are intentionally NOT flagged — counting chats is honest; only the
// talk/spoke/"conversation hours" phrasing over-claims.
const SPEECH_CLAIM: Record<string, RegExp> = {
  en: /\b(talk|talked|talking|spoke|spoken|speaking)\b|conversation hours/i,
  de: /gesprochen|gesprächszeit|gesprächsstunden|gesprächstag|h gespräch\b/i,
  es: /hablando|tiempo de conversación|horas de conversación|más conversación/i,
};

describe("Board active-time metric stays honestly labeled", () => {
  it("English hero label reads 'Active time', never 'Talk time'", () => {
    expect((en as { board_view: Board }).board_view.hero.talk_time).toBe(
      "Active time",
    );
  });

  it.each([
    ["en", en],
    ["de", de],
    ["es", es],
  ])("%s session-time labels make no speech claim", (lang, locale) => {
    const board = (locale as { board_view: Board }).board_view;
    for (const label of sessionTimeLabels(board)) {
      expect(label).not.toMatch(SPEECH_CLAIM[lang]);
    }
  });
});

/**
 * The "work hours" metric (board_view.stats.hours_saved + the
 * most_hours_saved_in_a_day record) is the summed wall-clock RUN TIME of
 * Sub-Jarvis (Jarvis-Agent) background tasks — jarvis/board/aggregator.py adds
 * payload.duration_s / 3600 per JarvisAgentTaskCompleted. That is how long the
 * sub-agent worked, NOT a measured amount of time the user saved. It used to
 * be labeled "hours saved" / "time-saver day", an unprovable benefit claim.
 */
const SAVINGS_CLAIM: Record<string, RegExp> = {
  en: /\bsaved\b|time-saver/i,
  de: /gespart|zeit-spar/i,
  es: /ahorrad|ahorro/i,
};

function workHoursLabels(board: Board): string[] {
  return [board.stats.hours_saved, board.records.most_hours_saved_in_a_day.title];
}

describe("Board work-hours metric does not claim unprovable savings", () => {
  it.each([
    ["en", en],
    ["de", de],
    ["es", es],
  ])("%s work-hours labels make no time-saved claim", (lang, locale) => {
    const board = (locale as { board_view: Board }).board_view;
    for (const label of workHoursLabels(board)) {
      expect(label).not.toMatch(SAVINGS_CLAIM[lang]);
    }
  });
});
