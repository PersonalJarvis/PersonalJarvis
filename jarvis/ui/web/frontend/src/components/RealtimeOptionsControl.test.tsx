import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key — matches the
// pattern used by CuModelSelector.test.tsx.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

const getRealtimeOptions = vi.fn();
const saveRealtimeOptions = vi.fn();
const fetchRealtimeVoicePreview = vi.fn();

vi.mock("@/hooks/useProviders", () => ({
  getRealtimeOptions: (...args: unknown[]) => getRealtimeOptions(...args),
  saveRealtimeOptions: (...args: unknown[]) => saveRealtimeOptions(...args),
  fetchRealtimeVoicePreview: (...args: unknown[]) =>
    fetchRealtimeVoicePreview(...args),
}));

import { RealtimeOptionsControl } from "./RealtimeOptionsControl";

const OPTIONS = {
  provider: "openai-realtime",
  models: [
    { id: "gpt-realtime", label: "GPT Realtime" },
    { id: "gpt-realtime-mini", label: "GPT Realtime Mini" },
  ],
  voices: [
    { id: "alloy", label: "Alloy" },
    { id: "echo", label: "Echo" },
  ],
  current_model: "",
  current_voice: "",
};

// jsdom implements neither media playback nor object URLs — stub the pieces
// the preview path touches so play() resolves and blobs get URLs.
beforeEach(() => {
  vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue();
  vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockReturnValue();
  URL.createObjectURL = vi.fn(() => "blob:mock");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

/** Open the voice picker panel via its trigger button. */
async function openVoicePanel() {
  const trigger = await screen.findByLabelText("apikeys_view.realtime_voice_label");
  fireEvent.click(trigger);
  return trigger;
}

describe("RealtimeOptionsControl", () => {
  it("renders a MODEL dropdown and a VOICE picker, populated from getRealtimeOptions", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    )) as HTMLSelectElement;

    expect(getRealtimeOptions).toHaveBeenCalledWith("openai-realtime");
    expect(
      Array.from(modelSelect.options).map((o) => o.value),
    ).toEqual(["", "gpt-realtime", "gpt-realtime-mini"]);

    await openVoicePanel();
    expect(screen.getByText("Alloy")).toBeTruthy();
    expect(screen.getByText("Echo")).toBeTruthy();
  });

  it("shows the 'Provider default' label when current_model/current_voice are empty", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    )) as HTMLSelectElement;
    const voiceTrigger = await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    );

    expect(modelSelect.value).toBe("");
    expect(voiceTrigger.textContent).toContain(
      "apikeys_view.realtime_provider_default",
    );
  });

  it("pre-selects the persisted current_model/current_voice", async () => {
    getRealtimeOptions.mockResolvedValue({
      ...OPTIONS,
      current_model: "gpt-realtime-mini",
      current_voice: "echo",
    });
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    )) as HTMLSelectElement;
    const voiceTrigger = await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    );

    await waitFor(() => expect(modelSelect.value).toBe("gpt-realtime-mini"));
    expect(voiceTrigger.textContent).toContain("Echo");
  });

  it("selecting a model calls saveRealtimeOptions(id, {model})", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    saveRealtimeOptions.mockResolvedValue({
      ok: true,
      provider: "openai-realtime",
      model: "gpt-realtime",
      voice: "",
      restart_required: false,
    });
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = await screen.findByLabelText("apikeys_view.realtime_model_label");
    fireEvent.change(modelSelect, { target: { value: "gpt-realtime" } });

    await waitFor(() =>
      expect(saveRealtimeOptions).toHaveBeenCalledWith("openai-realtime", {
        model: "gpt-realtime",
      }),
    );
  });

  it("clicking a voice name in the panel calls saveRealtimeOptions(id, {voice})", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    saveRealtimeOptions.mockResolvedValue({
      ok: true,
      provider: "openai-realtime",
      model: "",
      voice: "echo",
      restart_required: false,
    });
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    await openVoicePanel();
    fireEvent.click(screen.getByText("Echo"));

    await waitFor(() =>
      expect(saveRealtimeOptions).toHaveBeenCalledWith("openai-realtime", {
        voice: "echo",
      }),
    );
  });

  it("picking 'Provider default' again saves an explicit empty string", async () => {
    getRealtimeOptions.mockResolvedValue({
      ...OPTIONS,
      current_model: "gpt-realtime-mini",
      current_voice: "echo",
    });
    saveRealtimeOptions.mockResolvedValue({
      ok: true,
      provider: "openai-realtime",
      model: "",
      voice: "",
      restart_required: false,
    });
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    )) as HTMLSelectElement;
    await waitFor(() => expect(modelSelect.value).toBe("gpt-realtime-mini"));

    fireEvent.change(modelSelect, { target: { value: "" } });
    await waitFor(() =>
      expect(saveRealtimeOptions).toHaveBeenCalledWith("openai-realtime", { model: "" }),
    );

    await openVoicePanel();
    // getByText would also match the model <select>'s default <option> — the
    // panel's default entry is the only BUTTON carrying this label here.
    fireEvent.click(
      screen.getByRole("button", {
        name: "apikeys_view.realtime_provider_default",
      }),
    );
    await waitFor(() =>
      expect(saveRealtimeOptions).toHaveBeenCalledWith("openai-realtime", { voice: "" }),
    );
  });

  it("the trigger-row preview button samples the pinned voice WITHOUT saving", async () => {
    getRealtimeOptions.mockResolvedValue({
      ...OPTIONS,
      current_model: "gpt-realtime-mini",
      current_voice: "echo",
    });
    fetchRealtimeVoicePreview.mockResolvedValue(new Blob(["x"], { type: "audio/wav" }));
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    // Panel closed → the only preview button belongs to the pinned voice.
    const previewButton = await screen.findByLabelText("apikeys_voice.preview");
    fireEvent.click(previewButton);

    await waitFor(() =>
      expect(fetchRealtimeVoicePreview).toHaveBeenCalledWith({
        providerId: "openai-realtime",
        voice: "echo",
        language: "en",
        model: "gpt-realtime-mini",
      }),
    );
    expect(saveRealtimeOptions).not.toHaveBeenCalled();
  });

  it("every voice row in the open panel can be auditioned without saving", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    fetchRealtimeVoicePreview.mockResolvedValue(new Blob(["x"], { type: "audio/wav" }));
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    await openVoicePanel();
    // No pinned voice → no trigger-row preview; the buttons map 1:1 onto the
    // listed voices in catalog order (alloy, echo).
    const previewButtons = screen.getAllByLabelText("apikeys_voice.preview");
    expect(previewButtons.length).toBe(2);
    fireEvent.click(previewButtons[0]);

    await waitFor(() =>
      expect(fetchRealtimeVoicePreview).toHaveBeenCalledWith({
        providerId: "openai-realtime",
        voice: "alloy",
        language: "en",
        model: "",
      }),
    );
    expect(saveRealtimeOptions).not.toHaveBeenCalled();
  });

  it("the panel's language toggle switches the sample language", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    fetchRealtimeVoicePreview.mockResolvedValue(new Blob(["x"], { type: "audio/wav" }));
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    await openVoicePanel();
    fireEvent.click(screen.getByText("de"));
    const previewButtons = screen.getAllByLabelText("apikeys_voice.preview");
    fireEvent.click(previewButtons[1]);

    await waitFor(() =>
      expect(fetchRealtimeVoicePreview).toHaveBeenCalledWith({
        providerId: "openai-realtime",
        voice: "echo",
        language: "de",
        model: "",
      }),
    );
  });
});
