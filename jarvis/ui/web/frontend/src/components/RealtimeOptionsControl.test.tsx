import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  preview_available: true,
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

/** Open the model picker panel via its trigger button. */
async function openModelPanel() {
  const trigger = await screen.findByLabelText("apikeys_view.realtime_model_label");
  fireEvent.click(trigger);
  return trigger;
}

describe("RealtimeOptionsControl", () => {
  it("renders app-styled MODEL and VOICE pickers populated from the backend", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    await openModelPanel();
    expect(getRealtimeOptions).toHaveBeenCalledWith("openai-realtime");
    expect(screen.getByText("GPT Realtime")).toBeTruthy();
    expect(screen.getByText("GPT Realtime Mini")).toBeTruthy();

    await openVoicePanel();
    expect(screen.getByText("Alloy")).toBeTruthy();
    expect(screen.getByText("Echo")).toBeTruthy();
  });

  it("shows the 'Provider default' label when current_model/current_voice are empty", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelTrigger = await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    );
    const voiceTrigger = await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    );

    expect(modelTrigger.textContent).toContain(
      "apikeys_view.realtime_provider_default",
    );
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

    const modelTrigger = await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    );
    const voiceTrigger = await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    );

    await waitFor(() => expect(modelTrigger.textContent).toContain("GPT Realtime Mini"));
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

    await openModelPanel();
    fireEvent.click(screen.getByText("GPT Realtime"));

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

    const modelTrigger = await openModelPanel();
    await waitFor(() => expect(modelTrigger.textContent).toContain("GPT Realtime Mini"));
    fireEvent.click(
      screen.getByRole("button", {
        name: "apikeys_view.realtime_provider_default",
      }),
    );
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

  it("ignores preview audio that arrives after unmount", async () => {
    let resolvePreview: ((blob: Blob) => void) | undefined;
    getRealtimeOptions.mockResolvedValue({
      ...OPTIONS,
      current_voice: "echo",
    });
    fetchRealtimeVoicePreview.mockImplementationOnce(
      () =>
        new Promise<Blob>((resolve) => {
          resolvePreview = resolve;
        }),
    );
    const { unmount } = render(
      <RealtimeOptionsControl providerId="openai-realtime" />,
    );
    fireEvent.click(await screen.findByLabelText("apikeys_voice.preview"));
    await waitFor(() => expect(fetchRealtimeVoicePreview).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      resolvePreview?.(new Blob(["late"], { type: "audio/wav" }));
      await Promise.resolve();
    });

    expect(URL.createObjectURL).not.toHaveBeenCalled();
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

  it("keeps subscription voices selectable without rendering a broken preview", async () => {
    getRealtimeOptions.mockResolvedValue({
      provider: "codex-subscription-realtime",
      models: [{ id: "auto", label: "ChatGPT-Live (model chosen by OpenAI)" }],
      voices: [
        { id: "cove", label: "Cove" },
        { id: "juniper", label: "Juniper" },
      ],
      current_model: "auto",
      current_voice: "cove",
      preview_available: false,
    });
    saveRealtimeOptions.mockResolvedValue({
      ok: true,
      provider: "codex-subscription-realtime",
      model: "auto",
      voice: "juniper",
      restart_required: false,
    });

    render(<RealtimeOptionsControl providerId="codex-subscription-realtime" />);
    expect(
      await screen.findByText("ChatGPT-Live (model chosen by OpenAI)"),
    ).toBeTruthy();
    expect(screen.getByText("apikeys_view.realtime_model_managed")).toBeTruthy();
    expect(
      screen.getByText("apikeys_view.realtime_model_managed_hint"),
    ).toBeTruthy();
    await openVoicePanel();

    expect(screen.queryByLabelText("apikeys_voice.preview")).toBeNull();
    expect(screen.queryByText("de")).toBeNull();
    fireEvent.click(screen.getByText("Juniper"));
    await waitFor(() =>
      expect(saveRealtimeOptions).toHaveBeenCalledWith(
        "codex-subscription-realtime",
        { voice: "juniper" },
      ),
    );
    expect(fetchRealtimeVoicePreview).not.toHaveBeenCalled();
  });

  it("shows a recoverable error instead of silently hiding failed options", async () => {
    getRealtimeOptions
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    expect(
      await screen.findByText("apikeys_view.realtime_options_error"),
    ).toBeTruthy();
    fireEvent.click(screen.getByText("apikeys_view.realtime_options_retry"));

    expect(
      await screen.findByLabelText("apikeys_view.realtime_model_label"),
    ).toBeTruthy();
    expect(getRealtimeOptions).toHaveBeenCalledTimes(2);
  });
});
