import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppshotRecordingPanel } from "./AppshotRecordingPanel";
import type { AppshotSettings } from "@/lib/appshotApi";

const settings = { enabled: true, recording_hotkey: "ctrl+shift+9" } as AppshotSettings;
function setup(audio = true, phase = "idle") {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => ({
    phase, id: "", message: "", capability: { available: true, permission_required: false,
      detail: "", system_audio: { available: audio, detail: "" } },
  }) })));
  const save = vi.fn(async () => {});
  render(<AppshotRecordingPanel settings={settings} saving={false}
    onSettings={save} />);
  return save;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Recording quality preferences", () => {
  it("defaults to Full HD at 60 FPS and saves custom bitrate and audio together", async () => {
    const save = setup();
    const start = screen.getByTestId("appshots-recording-control");
    await waitFor(() => expect(start.hasAttribute("disabled")).toBe(false));
    expect(screen.getByLabelText("Resolution").textContent).toContain("1080p");
    expect(screen.getByLabelText("Frame rate").textContent).toContain("60");
    expect((screen.getByLabelText("Video bitrate (Mbps)") as HTMLInputElement).value).toBe("12");
    fireEvent.click(screen.getByLabelText("Resolution"));
    fireEvent.click(screen.getByRole("option", { name: /Native/i }));
    fireEvent.click(screen.getByLabelText("Frame rate"));
    fireEvent.click(screen.getByRole("option", { name: "120 FPS" }));
    fireEvent.change(screen.getByLabelText("Video bitrate (Mbps)"), { target: { value: "37" } });
    fireEvent.click(screen.getByLabelText("Include system audio"));
    await waitFor(() => expect(start.hasAttribute("disabled")).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "Save recording settings" }));
    expect(save).toHaveBeenCalledWith({ recording_resolution: "native", recording_fps: 120,
      recording_bitrate_mbps: 37, recording_system_audio: true });
  });

  it("does not submit invalid bitrates or enable unavailable system audio", async () => {
    const save = setup(false);
    await waitFor(() => expect(screen.getByTestId("appshots-recording-control").hasAttribute("disabled")).toBe(false));
    expect(screen.getByLabelText("Include system audio").hasAttribute("disabled")).toBe(true);
    fireEvent.change(screen.getByLabelText("Video bitrate (Mbps)"), { target: { value: "101" } });
    expect(screen.getByRole("button", { name: "Save recording settings" }).hasAttribute("disabled")).toBe(true);
    expect(save).not.toHaveBeenCalled();
  });

  it("locks preferences while capturing and keeps Stop available", async () => {
    setup(true, "recording");
    const stop = await screen.findByRole("button", { name: "Stop and save" });
    expect(stop.hasAttribute("disabled")).toBe(false);
    expect((screen.getByLabelText("Resolution").closest("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
    expect(screen.getByLabelText("Include system audio").hasAttribute("disabled")).toBe(true);
  });
});
