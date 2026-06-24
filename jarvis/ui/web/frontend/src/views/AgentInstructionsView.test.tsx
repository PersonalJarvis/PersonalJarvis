import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentInstructionsView } from "./AgentInstructionsView";

const STATE = {
  content: "Be terse.",
  exists: true,
  filename: "Ruben.md",
  template: "# How Ruben works with me\n## Do\n- ",
  char_count: 9,
};

const EMPTY_STATE = {
  content: "",
  exists: false,
  filename: "Ruben.md",
  template: "# How Ruben works with me\n## Do\n- ",
  char_count: 0,
};

afterEach(() => vi.restoreAllMocks());

describe("AgentInstructionsView", () => {
  it("renders the dynamic filename and loads the content", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => STATE }),
    );
    render(<AgentInstructionsView />);
    await waitFor(() =>
      expect(
        (screen.getByTestId("agent-instructions-editor") as HTMLTextAreaElement).value,
      ).toBe("Be terse."),
    );
    expect(screen.getByText("Ruben.md").textContent).toBe("Ruben.md");
  });

  it("PUTs the draft on save", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => STATE })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...STATE, content: "New rules.", ok: true, restart_required: false }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentInstructionsView />);
    const editor = await screen.findByTestId("agent-instructions-editor");
    fireEvent.change(editor, { target: { value: "New rules." } });
    fireEvent.click(screen.getByText("Save instructions"));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put![0]).toBe("/api/settings/agent-instructions");
      expect(JSON.parse((put![1] as RequestInit).body as string)).toEqual({
        content: "New rules.",
      });
    });
  });

  it("clears saved instructions only when the empty draft is saved", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => STATE })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ...EMPTY_STATE, ok: true, removed: true, restart_required: false }),
      });
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentInstructionsView />);
    const editor = await screen.findByTestId("agent-instructions-editor");
    fireEvent.change(editor, { target: { value: "" } });

    const save = screen.getByText("Save instructions").closest("button") as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    fireEvent.click(save);

    await waitFor(() => {
      const del = fetchMock.mock.calls.find(([, opts]) => opts?.method === "DELETE");
      expect(del).toBeTruthy();
      expect(del![0]).toBe("/api/settings/agent-instructions");
    });
  });

  it("loads the starter template into an empty editor", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => EMPTY_STATE }),
    );
    render(<AgentInstructionsView />);
    await screen.findByTestId("agent-instructions-editor");
    fireEvent.click(screen.getByText("Start from template"));
    await waitFor(() =>
      expect(
        (screen.getByTestId("agent-instructions-editor") as HTMLTextAreaElement).value,
      ).toBe(EMPTY_STATE.template),
    );
  });

  it("reverts unsaved changes without deleting the saved instructions", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => STATE });
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentInstructionsView />);
    const editor = await screen.findByTestId("agent-instructions-editor");
    fireEvent.change(editor, { target: { value: "Changed then regret" } });
    fireEvent.click(screen.getByText("Revert changes"));

    expect((editor as HTMLTextAreaElement).value).toBe("Be terse.");
    expect(fetchMock.mock.calls.some(([, opts]) => opts?.method === "DELETE")).toBe(false);
  });

  it("disables Save until the draft changes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => STATE }),
    );
    render(<AgentInstructionsView />);
    await screen.findByTestId("agent-instructions-editor");
    const save = screen.getByText("Save instructions").closest("button") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  });
});
