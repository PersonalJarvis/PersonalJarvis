/**
 * Component tests for the dictation history row's status badges.
 *
 * Both badges exist because the information was already being STORED and shown
 * nowhere. `cleanup_reason` is the older of the two: outside its three rule
 * languages the filler cleanup is a silent no-op, so anyone dictating in, say,
 * Japanese or Polish watched the filler switch sit ON while it had never once
 * run. `polish_status` is the same shape of promise — a model may rewrite what
 * the user said, and the row has to admit when it did and when it did not.
 *
 * The rows must also survive a backend that is newer or older than the bundle:
 * an unknown status renders as itself, a missing one renders nothing at all.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { DictationHistoryGroup } from "@/views/voice/DictationHistoryGroup";
import type { DictationEntry } from "@/hooks/useDictation";
import { setUiLanguage } from "@/i18n";

function entry(over: Partial<DictationEntry> = {}): DictationEntry {
  return {
    id: "d-1",
    created_at: "2026-07-29T09:15:00.000Z",
    raw_text: "so uh send the report",
    text: "send the report",
    language: "en",
    duration_s: 4.2,
    outcome: "inserted",
    method: "clipboard",
    removed_words: 0,
    cleanup_reason: "",
    word_count: 3,
    discarded: false,
    audio_available: false,
    error: null,
    ...over,
  };
}

function renderRows(entries: DictationEntry[]) {
  return render(
    <DictationHistoryGroup
      label="Today"
      entries={entries}
      onCopy={() => {}}
      onDiscard={() => {}}
      onRestore={() => {}}
      onDelete={() => {}}
      busyIds={new Set<string>()}
      copiedId={null}
    />,
  );
}

beforeEach(() => {
  setUiLanguage("en");
});

afterEach(cleanup);

describe("DictationHistoryGroup — wording badges", () => {
  it("says so when a model rewrote the text", () => {
    renderRows([
      entry({
        polish_status: "applied",
        polish_provider: "groq",
        polish_latency_ms: 240,
      }),
    ]);

    const badge = screen.getByTestId("dictation-polish-badge");
    expect(badge.textContent).toBe("Cleaned up");
    // The provider and what it cost belong in the tooltip, not in a chip that
    // has to sit next to a transcript.
    expect(badge.getAttribute("title")).toBe("groq · 240 ms");
  });

  it("says so when it fell back to the raw text", () => {
    renderRows([entry({ polish_status: "timeout" })]);

    expect(screen.getByTestId("dictation-polish-badge").textContent).toContain(
      "raw text",
    );
  });

  it("stays quiet when the pass is switched off", () => {
    renderRows([entry({ polish_status: "off" })]);

    expect(screen.queryByTestId("dictation-polish-badge")).toBeNull();
  });

  it("stays quiet on a row from before the pass existed", () => {
    renderRows([entry()]);

    expect(screen.queryByTestId("dictation-polish-badge")).toBeNull();
  });

  it("renders an unknown status as itself rather than a missing key", () => {
    renderRows([entry({ polish_status: "some_future_status" })]);

    expect(screen.getByTestId("dictation-polish-badge").textContent).toBe(
      "some_future_status",
    );
  });
});

describe("DictationHistoryGroup — cleanup-reason badge", () => {
  it("admits that filler removal has no rules for this language", () => {
    renderRows([entry({ language: "ja", cleanup_reason: "no_rules" })]);

    expect(
      screen.getByTestId("dictation-cleanup-reason-badge").textContent,
    ).toBe("No filler rules for this language");
  });

  it("admits that the destruction ceiling stopped it", () => {
    renderRows([entry({ cleanup_reason: "ceiling" })]);

    expect(
      screen.getByTestId("dictation-cleanup-reason-badge").textContent,
    ).toContain("skipped");
  });

  it("stays quiet when the cleanup ran, or when the user turned it off", () => {
    renderRows([entry({ cleanup_reason: "" })]);
    expect(screen.queryByTestId("dictation-cleanup-reason-badge")).toBeNull();
    cleanup();

    renderRows([entry({ cleanup_reason: "disabled" })]);
    expect(screen.queryByTestId("dictation-cleanup-reason-badge")).toBeNull();
  });

  it("translates both badges into the user's language", () => {
    setUiLanguage("de");
    renderRows([entry({ polish_status: "applied", cleanup_reason: "no_rules" })]);

    expect(screen.getByTestId("dictation-polish-badge").textContent).toBe(
      "Aufgeräumt", // i18n-allow: asserts the localized badge copy under test
    );
    expect(
      screen.getByTestId("dictation-cleanup-reason-badge").textContent,
    ).toBe("Keine Füllwort-Regeln für diese Sprache"); // i18n-allow: asserts the localized badge copy under test
  });
});
