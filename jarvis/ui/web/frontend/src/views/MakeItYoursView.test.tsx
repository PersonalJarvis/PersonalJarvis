import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key.
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

// Stub the heavy ChatsView import (we only need ViewHeader).
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

vi.mock("@/lib/workspaceApi", () => ({
  fetchWorkspaceAgents: vi.fn(),
  launchWorkspace: vi.fn(),
}));

// xterm.js needs a real canvas — stub the embedded terminal in jsdom.
vi.mock("@/components/workspace/WorkspaceTerminal", () => ({
  WorkspaceTerminal: ({ title }: { title: string }) => (
    <div data-testid="ws-term">{title}</div>
  ),
}));

import { MakeItYoursView } from "./MakeItYoursView";
import * as api from "@/lib/workspaceApi";

const RESPONSE: api.AgentsResponse = {
  cwd: "C:/proj",
  terminal_available: true,
  layout_choices: [1, 2, 4, 6, 8, 10, 12],
  agents: [
    {
      name: "claude",
      display_name: "Claude Code",
      installed: true,
      version: "2.1.195",
      install_command: "npm install -g @anthropic-ai/claude-code",
      launch_command: "claude",
    },
    {
      name: "codex",
      display_name: "Codex",
      installed: true,
      version: "0.142.3",
      install_command: "npm install -g @openai/codex",
      launch_command: "codex",
    },
  ],
};

function withResponse(overrides?: Partial<typeof RESPONSE>) {
  vi.mocked(api.fetchWorkspaceAgents).mockResolvedValue({ ...RESPONSE, ...overrides });
}

async function renderLoaded(overrides?: Partial<typeof RESPONSE>) {
  withResponse(overrides);
  render(<MakeItYoursView />);
  await waitFor(() => expect(api.fetchWorkspaceAgents).toHaveBeenCalled());
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MakeItYoursView", () => {
  it("loads agents and shows the layout tiles", async () => {
    await renderLoaded();
    expect(screen.getByText("make_it_yours.step_layout")).toBeTruthy();
    // BridgeSpace-style tiles for each choice.
    expect(screen.getByRole("button", { name: "8" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "12" })).toBeTruthy();
  });

  it("fills the workspace-size meter proportionally to the chosen terminal count", async () => {
    await renderLoaded();
    const fill = () => screen.getByTestId("terminal-fill") as HTMLElement;

    // Default layout is 1 → small fill, label "1 / 12".
    expect(screen.getByText("1 / 12")).toBeTruthy();

    // Pick 6 → meter exactly half.
    fireEvent.click(screen.getByRole("button", { name: "6" }));
    expect(screen.getByText("6 / 12")).toBeTruthy();
    expect(fill().style.width).toBe("50%");

    // Pick 12 → meter full (the bug was: stayed the same length for every count).
    fireEvent.click(screen.getByRole("button", { name: "12" }));
    expect(screen.getByText("12 / 12")).toBeTruthy();
    expect(fill().style.width).toBe("100%");
  });

  it("disables Next until the agent counts sum to the chosen layout", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByRole("button", { name: "8" })); // choose 8
    fireEvent.click(screen.getByRole("button", { name: "make_it_yours.next" }));

    // Default split fills all 8 with Claude → 8/8 → Next enabled.
    const next = screen.getByRole("button", { name: "make_it_yours.next" });
    expect((next as HTMLButtonElement).disabled).toBe(false);

    // Remove one → 7/8 → Next disabled.
    fireEvent.click(screen.getAllByLabelText("decrease")[0]);
    expect((next as HTMLButtonElement).disabled).toBe(true);

    // "Split evenly" → 4+4 = 8 → Next enabled again.
    fireEvent.click(screen.getByText("make_it_yours.split_evenly"));
    expect((next as HTMLButtonElement).disabled).toBe(false);
  });

  it("blocks launch and offers install when a chosen agent is missing", async () => {
    await renderLoaded({
      agents: [
        RESPONSE.agents[0],
        { ...RESPONSE.agents[1], installed: false, version: null },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "make_it_yours.next" }));

    // Assign both to the not-installed Codex.
    fireEvent.click(screen.getByText("make_it_yours.all_codex"));

    expect(screen.getByText("make_it_yours.install")).toBeTruthy();
    const next = screen.getByRole("button", { name: "make_it_yours.next" });
    expect((next as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows a summary with the folder on the confirm step", async () => {
    await renderLoaded();
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "make_it_yours.next" })); // → agents
    fireEvent.click(screen.getByRole("button", { name: "make_it_yours.next" })); // → confirm

    expect(screen.getByText(/2 × Claude Code/)).toBeTruthy();
    expect(screen.getByText("C:/proj")).toBeTruthy();
    expect(screen.getByRole("button", { name: /make_it_yours\.launch/ })).toBeTruthy();
  });

  it("warns and cannot launch when no desktop terminal is available", async () => {
    await renderLoaded({ terminal_available: false });
    expect(screen.getByText("make_it_yours.no_terminal")).toBeTruthy();
  });

  it("opens an in-app terminal grid after launching", async () => {
    await renderLoaded();
    vi.mocked(api.launchWorkspace).mockResolvedValue({
      ok: true,
      cwd: "C:/proj",
      slots: [
        { index: 0, agent: "claude", display_name: "Claude Code" },
        { index: 1, agent: "claude", display_name: "Claude Code" },
      ],
      trust: [],
      detail: "",
    });

    fireEvent.click(screen.getByRole("button", { name: "2" }));
    fireEvent.click(screen.getByRole("button", { name: "make_it_yours.next" })); // → agents
    fireEvent.click(screen.getByRole("button", { name: "make_it_yours.next" })); // → confirm
    fireEvent.click(screen.getByRole("button", { name: /make_it_yours\.launch/ }));

    await waitFor(() => expect(screen.getAllByTestId("ws-term")).toHaveLength(2));
    expect(screen.getByText("make_it_yours.end_session")).toBeTruthy();
    expect(api.launchWorkspace).toHaveBeenCalledWith(2, { claude: 2, codex: 0 });
  });
});
