/**
 * The transcript detail is half of a master-detail pair. When the view is too
 * narrow to show both columns it owns the whole width, and then it has to
 * carry the way back to the session rail — on every state it can land in,
 * including one that is still loading or failed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { SessionDetail } from "./SessionDetail";
import type { SessionDetail as SessionDetailModel } from "./types";

const TRANSLATIONS: Record<string, string> = {
  "session_detail.back_to_sessions": "Back to sessions",
  "session_detail.title": "Voice session",
  "session_detail.turns": "turns",
  "session_detail.loading": "Loading session",
  "session_detail.load_error": "Failed to load",
  "sessions.select_one": "Pick a session",
  "sessions.no_turns": "No turns",
  "session_detail.no_turns_suffix": " recorded.",
  "session_list.hangup_hotkey": "Hotkey",
};

vi.mock("@/i18n", () => ({
  translate: (key: string) => TRANSLATIONS[key] ?? key,
  useT: () => (key: string) => TRANSLATIONS[key] ?? key,
  useUiLanguage: () => "en",
}));

vi.mock("@/hooks/useCapabilities", () => ({
  useCapabilities: () => ({
    data: { native_file_actions: false, platform: "linux" },
  }),
}));

vi.mock("@/hooks/useOutputs", () => ({
  useOpeners: () => ({ data: [], isLoading: false }),
  usePreferredOpener: () => ({ data: "" }),
  useSetPreferredOpener: () => ({ mutate: vi.fn() }),
}));

afterEach(cleanup);

function detail(): SessionDetailModel {
  return {
    session: {
      id: "sess-1",
      started_ms: Date.now() - 120_000,
      ended_ms: Date.now() - 60_000,
      hangup_reason: "hotkey",
      turn_count: 0,
      total_cost_usd: 0,
      total_tokens_in: 0,
      total_tokens_out: 0,
      providers_used: [],
      language: "en",
      wake_keyword: "Jarvis",
      voice_mode: "pipeline",
    },
    turns: [],
    events: [],
  };
}

describe("SessionDetail stacked layout", () => {
  it("offers the way back when the rail is off screen", () => {
    const onBack = vi.fn();
    render(
      <SessionDetail
        detail={detail()}
        loading={false}
        error={null}
        onBack={onBack}
      />,
    );

    fireEvent.click(screen.getByText("Back to sessions"));

    expect(onBack).toHaveBeenCalledTimes(1);
  });

  it("shows no way back while both columns are on screen", () => {
    render(<SessionDetail detail={detail()} loading={false} error={null} />);

    expect(screen.queryByText("Back to sessions")).toBeNull();
  });

  it.each([
    [
      "still loading",
      { detail: undefined, loading: true, error: null },
      "Loading session",
    ],
    [
      "failed to load",
      { detail: undefined, loading: false, error: new Error("boom") },
      "Failed to load",
    ],
    [
      "showing nothing yet",
      { detail: undefined, loading: false, error: null },
      "Pick a session",
    ],
  ])("is no dead end while %s", (_name, props, marker) => {
    // Without this the narrow layout traps the reader: a session that never
    // loads would leave no rail and no way back to it.
    render(
      <SessionDetail
        detail={props.detail}
        loading={props.loading}
        error={props.error}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByText(marker)).toBeTruthy();
    expect(screen.getByText("Back to sessions")).toBeTruthy();
  });
});
