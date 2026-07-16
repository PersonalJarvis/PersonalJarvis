import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionDetail } from "./SessionDetail";
import { SessionList } from "./SessionList";
import type {
  SessionDetail as SessionDetailModel,
  SessionListItem,
  VoiceMode,
} from "./types";
import { VoiceModeBadge } from "./VoiceModeBadge";

const TRANSLATIONS: Record<string, string> = {
  "voice_mode.label": "Mode",
  "voice_mode.realtime": "Realtime",
  "voice_mode.pipeline": "Pipeline",
  "voice_mode.unknown": "Unknown",
  "session_detail.title": "Voice session",
  "session_detail.turns": "turns",
  "sessions.no_turns": "No turns",
  "session_detail.no_turns_suffix": " recorded.",
  "session_list.turns": "turns",
  "session_list.ago_prefix": "",
  "session_list.ago_suffix": "ago",
  "session_list.unit_sec": "s",
  "session_list.unit_min": "min",
  "session_list.unit_hour": "h",
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

function session(mode: VoiceMode, id = mode): SessionListItem {
  return {
    id,
    started_ms: Date.now() - 120_000,
    ended_ms: Date.now() - 60_000,
    hangup_reason: "hotkey",
    turn_count: 1,
    total_cost_usd: 0,
    total_tokens_in: 0,
    total_tokens_out: 0,
    providers_used: [],
    language: "en",
    wake_keyword: "Jarvis",
    voice_mode: mode,
    duration_s: 60,
    preview: `${mode} transcript`,
  };
}

function detail(mode: VoiceMode): SessionDetailModel {
  const { duration_s: _duration, preview: _preview, ...sessionRow } =
    session(mode);
  return { session: sessionRow, turns: [], events: [] };
}

describe("VoiceModeBadge", () => {
  it.each([
    ["realtime", "Realtime"],
    ["pipeline", "Pipeline"],
    ["unknown", "Unknown"],
  ] as const)("renders the %s mode with text and an icon", (mode, label) => {
    render(<VoiceModeBadge mode={mode} />);

    const badge = screen.getByLabelText(`Mode: ${label}`);
    expect(badge.getAttribute("data-voice-mode")).toBe(mode);
    expect(within(badge).getByText(label)).toBeTruthy();
    expect(badge.querySelector('svg[aria-hidden="true"]')).toBeTruthy();
  });

  it("degrades an unfamiliar API value to the neutral unknown presentation", () => {
    render(<VoiceModeBadge mode="future-mode" />);

    expect(screen.getByLabelText("Mode: Unknown").getAttribute("data-voice-mode")).toBe(
      "unknown",
    );
  });
});

describe("session mode locations", () => {
  it("shows a compact indicator in every session-list row", () => {
    render(
      <SessionList
        sessions={[
          session("realtime"),
          session("pipeline"),
          session("unknown"),
        ]}
        selectedId="realtime"
        onSelect={vi.fn()}
        loading={false}
      />,
    );

    for (const label of ["Realtime", "Pipeline", "Unknown"]) {
      const badge = screen.getByLabelText(`Mode: ${label}`);
      expect(badge.closest("button")).toBeTruthy();
      expect(within(badge).getByText(label)).toBeTruthy();
    }
  });

  it.each([
    ["realtime", "Realtime"],
    ["pipeline", "Pipeline"],
    ["unknown", "Unknown"],
  ] as const)(
    "shows a prominent labeled %s indicator in the detail header",
    (mode, label) => {
      render(
        <SessionDetail detail={detail(mode)} loading={false} error={null} />,
      );

      const titleRow = screen.getByText("Voice session").parentElement;
      expect(titleRow).toBeTruthy();
      const badge = within(titleRow as HTMLElement).getByLabelText(
        `Mode: ${label}`,
      );
      expect(within(badge).getByText("Mode")).toBeTruthy();
      expect(within(badge).getByText(label)).toBeTruthy();
      expect(badge.querySelector('svg[aria-hidden="true"]')).toBeTruthy();
    },
  );
});
