import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key — matches the
// pattern used by CuModelSelector.test.tsx.
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

const getRealtimeOptions = vi.fn();
const saveRealtimeOptions = vi.fn();

vi.mock("@/hooks/useProviders", () => ({
  getRealtimeOptions: (...args: unknown[]) => getRealtimeOptions(...args),
  saveRealtimeOptions: (...args: unknown[]) => saveRealtimeOptions(...args),
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("RealtimeOptionsControl", () => {
  it("renders a MODEL and a VOICE dropdown, populated from getRealtimeOptions", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    )) as HTMLSelectElement;
    const voiceSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    )) as HTMLSelectElement;

    expect(getRealtimeOptions).toHaveBeenCalledWith("openai-realtime");
    expect(
      Array.from(modelSelect.options).map((o) => o.value),
    ).toEqual(["", "gpt-realtime", "gpt-realtime-mini"]);
    expect(
      Array.from(voiceSelect.options).map((o) => o.value),
    ).toEqual(["", "alloy", "echo"]);
  });

  it("maps the leading option to the 'Provider default' label and selects it when current_model/current_voice are empty", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const modelSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_model_label",
    )) as HTMLSelectElement;
    const voiceSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    )) as HTMLSelectElement;

    expect(modelSelect.value).toBe("");
    expect(voiceSelect.value).toBe("");
    expect(screen.getAllByText("apikeys_view.realtime_provider_default").length).toBe(2);
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
    const voiceSelect = (await screen.findByLabelText(
      "apikeys_view.realtime_voice_label",
    )) as HTMLSelectElement;

    await waitFor(() => expect(modelSelect.value).toBe("gpt-realtime-mini"));
    expect(voiceSelect.value).toBe("echo");
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

  it("selecting a voice calls saveRealtimeOptions(id, {voice})", async () => {
    getRealtimeOptions.mockResolvedValue(OPTIONS);
    saveRealtimeOptions.mockResolvedValue({
      ok: true,
      provider: "openai-realtime",
      model: "",
      voice: "echo",
      restart_required: false,
    });
    render(<RealtimeOptionsControl providerId="openai-realtime" />);

    const voiceSelect = await screen.findByLabelText("apikeys_view.realtime_voice_label");
    fireEvent.change(voiceSelect, { target: { value: "echo" } });

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
  });
});
