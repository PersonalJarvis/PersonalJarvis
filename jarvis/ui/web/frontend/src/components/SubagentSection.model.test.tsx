import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key (assert keys exactly).
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

import { SubagentSection } from "./SubagentSection";

const STATUS = {
  configured: true,
  enabled: true,
  binary_path: "openclaw",
  binary_detected: "/usr/bin/openclaw",
  version_pin: null,
  time_cap_min: null,
  concurrency: null,
  state_dir_root: null,
  brain_primary: "claude-api",
  provider_slug: "claude-cli",
  model_override: null,
  sub_model_override: "claude-sonnet-4-6",
  model_resolved: "claude-cli/claude-sonnet-4-6",
  mapping: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SubagentSection — dedicated subagent LLM model", () => {
  it("shows the model input prefilled with the current override", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => STATUS }),
    );
    render(<SubagentSection />);

    const input = (await screen.findByLabelText(
      "subagent_model.model_label",
    )) as HTMLInputElement;
    expect(input.value).toBe("claude-sonnet-4-6");
  });

  it("saves a new model via POST /api/subagent/model", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes("/api/subagent/model")) {
        return {
          ok: true,
          json: async () => ({ ok: true, model: "claude-opus-4-8", persisted: true }),
        };
      }
      return { ok: true, json: async () => STATUS };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SubagentSection />);

    const input = (await screen.findByLabelText(
      "subagent_model.model_label",
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "claude-opus-4-8" } });
    fireEvent.click(screen.getByText("subagent_model.apply"));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes("/api/subagent/model"),
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        model: "claude-opus-4-8",
        persist: true,
      });
    });
  });

  it("explains that subagents reuse the Brain keys, and locks a keyless provider", async () => {
    const statusWithMapping = {
      ...STATUS,
      mapping: [
        {
          jarvis: "openai",
          openclaw: "openai",
          env_var: "OPENAI_API_KEY",
          env_fallback: null,
          key_set: false,
          is_active_brain: false,
        },
      ],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => statusWithMapping }),
    );
    render(<SubagentSection />);

    // The section-level coupling explainer points at the Brain section above.
    await waitFor(() =>
      expect(screen.getByText(/Subagents reuse the API keys/i)).toBeTruthy(),
    );

    // A provider without a key shows a clear "Locked — add the key above" hint.
    expect(screen.getByText(/Locked/i)).toBeTruthy();
    expect(screen.getByText(/unlock it/i)).toBeTruthy();
  });

  it("empty input is a valid reset to the provider default", async () => {
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes("/api/subagent/model")) {
        return { ok: true, json: async () => ({ ok: true, model: "", persisted: true }) };
      }
      return { ok: true, json: async () => STATUS };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<SubagentSection />);

    const input = (await screen.findByLabelText(
      "subagent_model.model_label",
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByText("subagent_model.apply"));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        (c) => String(c[0]).includes("/api/subagent/model"),
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string).model).toBe("");
    });
  });
});
