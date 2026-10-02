import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoiceComposer } from "@/components/home/VoiceComposer";

const call = { active: false, busy: false, connecting: false, toggleCall: vi.fn() };

vi.mock("@/components/agentic/useVoiceCall", () => ({ useVoiceCall: () => call }));
vi.mock("@/hooks/useVoiceReadiness", () => ({ useVoiceReadiness: () => ({ connected: true }) }));
vi.mock("@/hooks/useVoiceEngineDisplay", () => ({
  useVoiceEngineDisplay: () => ({ providerLabel: "OpenAI Realtime", model: "gpt-realtime" }),
}));
vi.mock("@/hooks/usePromptMode", () => ({
  usePromptMode: () => ({ enabled: null, busy: false, toggle: async () => {} }),
}));

afterEach(() => {
  cleanup();
  call.active = false;
  call.toggleCall.mockReset();
});

describe("VoiceComposer", () => {
  it("offers Start while no call runs and says what to do", () => {
    render(<VoiceComposer hint="Press Start to talk" />);
    expect(screen.getByTestId("voice-hint").textContent).toBe("Press Start to talk");
    fireEvent.click(screen.getByTestId("voice-call-start"));
    expect(call.toggleCall).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("voice-call-stop")).toBeNull();
  });

  it("turns the control into Stop while a call is open", () => {
    call.active = true;
    render(<VoiceComposer hint="Speaking…" />);
    fireEvent.click(screen.getByTestId("voice-call-stop"));
    expect(call.toggleCall).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("voice-call-start")).toBeNull();
  });

  it("shows the way back to typing only when the host has one", () => {
    render(<VoiceComposer hint="" />);
    expect(screen.queryByTestId("voice-mode-exit")).toBeNull();
    cleanup();
    const onExit = vi.fn();
    render(<VoiceComposer hint="" onExit={onExit} />);
    fireEvent.click(screen.getByTestId("voice-mode-exit"));
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("names the voice engine under the card", () => {
    render(<VoiceComposer hint="" />);
    expect(screen.getByTestId("voice-engine").textContent).toContain("OpenAI Realtime");
  });
});
