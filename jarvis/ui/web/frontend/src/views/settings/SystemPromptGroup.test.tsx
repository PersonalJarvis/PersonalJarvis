import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SystemPromptGroup } from "./SystemPromptGroup";

const DEFAULT_PROMPT = "You are JARVIS — the default persona.";

const DEFAULT_STATE = {
  content: DEFAULT_PROMPT,
  is_custom: false,
  default: DEFAULT_PROMPT,
  char_count: DEFAULT_PROMPT.length,
};

const CUSTOM_STATE = {
  content: "You are NOVA, a custom assistant.",
  is_custom: true,
  default: DEFAULT_PROMPT,
  char_count: "You are NOVA, a custom assistant.".length,
};

afterEach(() => vi.restoreAllMocks());

describe("SystemPromptGroup", () => {
  it("renders the heading and loads the current prompt into the editor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => DEFAULT_STATE }),
    );
    render(<SystemPromptGroup />);

    expect(screen.getByText("System Prompt")).toBeTruthy();

    await waitFor(() => {
      const editor = screen.getByTestId("system-prompt-editor") as HTMLTextAreaElement;
      expect(editor.value).toBe(DEFAULT_PROMPT);
    });
    // No custom override yet → the "Default" state is shown.
    expect(screen.getByText("Default")).toBeTruthy();
  });

  it("PUTs the edited content when Save is clicked", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => DEFAULT_STATE })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...CUSTOM_STATE, ok: true, restart_required: false }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemPromptGroup />);

    const editor = await waitFor(
      () => screen.getByTestId("system-prompt-editor") as HTMLTextAreaElement,
    );
    fireEvent.change(editor, { target: { value: "You are NOVA, a custom assistant." } });

    fireEvent.click(screen.getByText("Save prompt"));

    await waitFor(() => {
      const putCall = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(putCall).toBeTruthy();
      expect(putCall?.[0]).toBe("/api/settings/system-prompt");
      expect(JSON.parse(putCall?.[1]?.body as string)).toMatchObject({
        content: "You are NOVA, a custom assistant.",
      });
    });
  });

  it("DELETEs to reset and reloads the default into the editor", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => CUSTOM_STATE })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...DEFAULT_STATE, ok: true, removed: true }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<SystemPromptGroup />);

    // Starts with a custom override.
    await waitFor(() => expect(screen.getByText("Custom")).toBeTruthy());

    fireEvent.click(screen.getByText("Reset to default"));

    await waitFor(() => {
      const delCall = fetchMock.mock.calls.find(([, opts]) => opts?.method === "DELETE");
      expect(delCall).toBeTruthy();
      expect(delCall?.[0]).toBe("/api/settings/system-prompt");
    });
    // After reset the editor shows the default again.
    await waitFor(() => {
      const editor = screen.getByTestId("system-prompt-editor") as HTMLTextAreaElement;
      expect(editor.value).toBe(DEFAULT_PROMPT);
    });
  });

  it("does not crash when the response is missing the content field", async () => {
    // SettingsView mounts several panels that share one fetch mock; a response
    // shaped for another endpoint (no `content`) must not throw.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ phrase: "" }) }),
    );
    render(<SystemPromptGroup />);

    await waitFor(() => {
      const editor = screen.getByTestId("system-prompt-editor") as HTMLTextAreaElement;
      expect(editor.value).toBe("");
    });
    // Heading still renders — the panel degraded gracefully.
    expect(screen.getByText("System Prompt")).toBeTruthy();
  });

  it("disables Save until the prompt is edited", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => DEFAULT_STATE }),
    );
    render(<SystemPromptGroup />);

    const save = await waitFor(() => screen.getByText("Save prompt"));
    expect(save.closest("button")?.hasAttribute("disabled")).toBe(true);

    const editor = screen.getByTestId("system-prompt-editor") as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: "something new" } });
    await waitFor(() =>
      expect(save.closest("button")?.hasAttribute("disabled")).toBe(false),
    );
  });
});
