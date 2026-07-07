import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AudioDevicesGroup } from "./AudioDevicesGroup";

// Mirrors GET /api/settings/audio-devices on a desktop with two outputs and
// one microphone; the headset output is the persisted pick.
const DESKTOP = {
  available: true,
  auto_value: "auto-headset",
  outputs: [
    { name: "Speakers (Realtek HD Audio)", is_default: true },
    { name: "PRO X Gaming Headset", is_default: false },
  ],
  inputs: [{ name: "Microphone (PRO X)", is_default: true }],
  selected_output: "PRO X Gaming Headset",
  selected_input: "auto-headset",
};

// A headless host: no devices at all — the card degrades to a caption.
const HEADLESS = {
  available: false,
  auto_value: "auto-headset",
  outputs: [],
  inputs: [],
  selected_output: "auto-headset",
  selected_input: "auto-headset",
};

afterEach(() => vi.restoreAllMocks());

describe("AudioDevicesGroup", () => {
  it("renders both pickers with the persisted selection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => DESKTOP }),
    );
    render(<AudioDevicesGroup />);

    expect(screen.getByText("Audio devices")).toBeTruthy();

    await waitFor(() => {
      const output = screen.getByTestId(
        "audio-output-select",
      ) as HTMLSelectElement;
      expect(output.value).toBe("PRO X Gaming Headset");
      const input = screen.getByTestId(
        "audio-input-select",
      ) as HTMLSelectElement;
      expect(input.value).toBe("auto-headset");
    });

    // The OS default entry is labeled, the automatic option is first.
    expect(screen.getByText(/Speakers \(Realtek HD Audio\)/)).toBeTruthy();
    const autoOptions = screen.getAllByText("Automatic (recommended)");
    expect(autoOptions.length).toBe(2);
  });

  it("PUTs the picked output device", async () => {
    const fetchMock = vi
      .fn()
      // initial GET
      .mockResolvedValueOnce({ ok: true, json: async () => DESKTOP })
      // PUT after pick
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ok: true,
          selected_output: "Speakers (Realtek HD Audio)",
          selected_input: "auto-headset",
          persisted: true,
          applied_live: true,
          restart_required: false,
        }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<AudioDevicesGroup />);

    const output = (await waitFor(() =>
      screen.getByTestId("audio-output-select"),
    )) as HTMLSelectElement;
    fireEvent.change(output, {
      target: { value: "Speakers (Realtek HD Audio)" },
    });

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(
        ([, opts]) => opts?.method === "PUT",
      );
      expect(putCall).toBeTruthy();
      expect(putCall?.[0]).toBe("/api/settings/audio-devices");
      expect(JSON.parse(putCall?.[1]?.body as string)).toMatchObject({
        output_device: "Speakers (Realtek HD Audio)",
        persist: true,
      });
    });
  });

  it("degrades to an honest caption on a headless host", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => HEADLESS }),
    );
    render(<AudioDevicesGroup />);

    await waitFor(() =>
      expect(screen.getByText(/No audio devices found/i)).toBeTruthy(),
    );
    expect(screen.queryByTestId("audio-output-select")).toBeNull();
    expect(screen.queryByTestId("audio-input-select")).toBeNull();
  });

  it("keeps an unplugged persisted name visible as the selected value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ...DESKTOP,
          selected_output: "Jabra Evolve2 65",
        }),
      }),
    );
    render(<AudioDevicesGroup />);

    await waitFor(() => {
      const output = screen.getByTestId(
        "audio-output-select",
      ) as HTMLSelectElement;
      expect(output.value).toBe("Jabra Evolve2 65");
    });
  });
});
