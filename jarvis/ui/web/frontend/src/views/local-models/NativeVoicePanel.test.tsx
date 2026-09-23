import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { NativeVoiceLibrary } from "@/hooks/useNativeVoiceModels";

const state = vi.hoisted(() => ({
  data: { entries: [], active_job: null, custom_manifest_schema: {} } as NativeVoiceLibrary,
  acquire: vi.fn(), cancel: vi.fn(),
}));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/hooks/useNativeVoiceModels", () => ({
  useNativeVoiceModels: () => ({
    library: { data: state.data, isPending: false, isError: false, isFetching: false, refetch: vi.fn() },
    acquire: { mutate: state.acquire, isPending: false, isError: false },
    cancel: { mutate: state.cancel, isPending: false, isError: false },
  }),
}));
import { NativeVoicePanel } from "./NativeVoicePanel";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

function library(): NativeVoiceLibrary {
  return { entries: [{ model_id: "voice", fingerprint: "identity", label: "Test voice",
    family: "test", languages: ["en"], download_bytes: 100, declared_capabilities: [],
    package_present: false, runtime_qualified: false, job: null }], active_job: null, custom_manifest_schema: {} };
}

describe("native voice packages", () => {
  it("imports selected manifest data and the source folder, without a server command", async () => {
    state.data = library();
    render(<NativeVoicePanel />);
    const manifest = { id: "own-voice", source: { kind: "local" } };
    const file = { size: 120, text: async () => JSON.stringify(manifest) };
    fireEvent.change(screen.getByLabelText("local_models.native.manifest"), { target: { files: [file] } });
    fireEvent.change(screen.getByRole("textbox", { name: "local_models.native.folder" }), { target: { value: "/models/own" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "local_models.native.import" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "local_models.native.import" }));
    expect(state.acquire).toHaveBeenCalledWith({ manifest, source_directory: "/models/own" });
  });

  it("rejects invalid manifest files without sending their contents", async () => {
    state.data = library();
    render(<NativeVoicePanel />);
    fireEvent.change(screen.getByLabelText("local_models.native.manifest"), { target: { files: [{ size: 20, text: async () => "invalid-json" }] } });
    await screen.findByRole("alert");
    expect(state.acquire).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "local_models.native.import" }).hasAttribute("disabled")).toBe(true);
  });
  it("downloads the selected package and never offers activation before qualification", () => {
    state.data = library();
    render(<NativeVoicePanel />);
    fireEvent.click(screen.getByRole("button", { name: "local_models.native.download" }));
    expect(state.acquire).toHaveBeenCalledWith({ fingerprint: "identity" });
    expect(screen.getByText("local_models.native.preview")).toBeDefined();
    expect(screen.queryByRole("button", { name: /activate|use model/i })).toBeNull();
  });

  it("blocks another download while cancellation is pending and shows progress", () => {
    state.data = library();
    const job = { id: "job", fingerprint: "identity", phase: "acquiring" as const,
      completed_bytes: 50, total_bytes: 100, error: "" };
    state.data.active_job = job;
    state.data.entries[0].job = job;
    const view = render(<NativeVoicePanel />);
    expect(screen.getByRole("button", { name: "local_models.native.download" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("progressbar").getAttribute("value")).toBe("50");
    fireEvent.click(screen.getByRole("button", { name: "local_models.native.cancel" }));
    expect(state.cancel).toHaveBeenCalledWith("job");
    state.data.entries[0].job = { ...job, phase: "cancelling" };
    view.rerender(<NativeVoicePanel />);
    expect(screen.getByRole("button", { name: "local_models.native.cancel" }).hasAttribute("disabled")).toBe(true);
  });

  it("labels stored weights without calling the model ready", () => {
    state.data = library();
    state.data.entries[0].package_present = true;
    render(<NativeVoicePanel />);
    expect(screen.getByText("local_models.native.stored")).toBeDefined();
    expect(screen.getByRole("button", { name: "local_models.native.verify" })).toBeDefined();
  });
});
