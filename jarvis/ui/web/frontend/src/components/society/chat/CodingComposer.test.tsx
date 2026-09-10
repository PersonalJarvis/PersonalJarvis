import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SocietyAgent } from "../data";
import { fetchIdeAgents } from "@/lib/agenticIdeApi";
import { Composer } from "./AgentChatPanel";

vi.mock("../data", async (load) => ({
  ...await load<typeof import("../data")>(),
  useSocietyCapabilities: () => ({ data: [], isLoading: false }),
}));
vi.mock("@/lib/agenticIdeApi", async (load) => ({
  ...await load<typeof import("@/lib/agenticIdeApi")>(), fetchIdeAgents: vi.fn(),
}));
vi.mock("@/components/agentic/FolderPicker", () => ({
  FolderPicker: ({ onSelect }: { onSelect: (path: string) => void }) =>
    <button onClick={() => onSelect("/projects/chosen")}>Example project</button>,
}));

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchIdeAgents).mockResolvedValue({
    terminal_available: true, max_terminals: 12, suggested_names: ["T1"],
    agents: [
      { name: "codex", display_name: "Codex", installed: true, accepts_prompts: true, version: null, install_command: null },
      { name: "future-cli", display_name: "Future CLI", installed: true, accepts_prompts: true, custom: true, version: null, install_command: null },
      { name: "shell", display_name: "Terminal", kind: "shell", installed: true, version: null, install_command: null },
    ],
  });
});

function mount(send = vi.fn().mockResolvedValue(undefined)) {
  render(<Composer agent={{ name: "Jarvis" } as SocietyAgent} mentionable={[]} busy={false}
    sessionId={null} cwd="/personal-workspace" provider="openrouter" onSend={send} onCancel={async () => {}} />);
  return send;
}

function writeDraft(box: HTMLElement, text: string) {
  box.textContent = text;
  box.focus();
  const range = document.createRange();
  range.selectNodeContents(box);
  range.collapse(false);
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  fireEvent.input(box);
}

async function chooseCodex() {
  const box = screen.getByRole("textbox") as HTMLDivElement;
  writeDraft(box, "@");
  const row = await screen.findByText("Codex");
  fireEvent.click(row);
  await waitFor(() => expect(box.textContent).toContain("@codex"));
  expect(screen.getByRole("status").textContent).toContain("Assignment for");
  expect(screen.getByTestId("agent-mark-codex").getAttribute("data-logo")).toBe("/provider-logos/openai.svg");
  return box;
}

describe("coding assignment composer", () => {
  it("opens the coding group from Add using the same short tags", async () => {
    mount();
    fireEvent.click(screen.getByLabelText("More"));
    fireEvent.click(screen.getByText("Coding Agents"));
    fireEvent.click(await screen.findByText("Codex"));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "" }).textContent).toContain("@codex"));
  });
  it("loads the coding group on @ and sends the CLI and explicit project as one draft", async () => {
    const send = mount();
    expect(fetchIdeAgents).not.toHaveBeenCalled();
    const box = await chooseCodex();
    expect(send).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Project folder (optional)"), { target: { value: "/projects/site" } });
    writeDraft(box, "@codex Fix the tests");
    fireEvent.click(screen.getByLabelText("Send"));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const text = send.mock.calls[0][0];
    expect(text).toContain("core:coding-session");
    expect(text).toContain('CLI IDs: ["codex"]');
    expect(text).toContain('Project directory: "/projects/site"');
    expect(text).not.toContain("[to jarvis]");
  });

  it("allows naming the folder in the message without adopting the personal workspace", async () => {
    const send = mount();
    const box = await chooseCodex();
    writeDraft(box, "@codex Work in /projects/site");
    fireEvent.click(screen.getByLabelText("Send"));
    await waitFor(() => expect(send).toHaveBeenCalled());
    expect(send.mock.calls[0][0]).toContain("directory specified in the user's message");
    expect(send.mock.calls[0][0]).not.toContain("/personal-workspace");
  });

  it("uses the existing folder picker only after the user asks for it", async () => {
    mount();
    await chooseCodex();
    fireEvent.click(screen.getByText("Choose folder"));
    fireEvent.click(await screen.findByText("Example project"));
    fireEvent.click(screen.getByText("Use folder"));
    expect((screen.getByLabelText("Project folder (optional)") as HTMLInputElement).value).toBe("/projects/chosen");
  });
});
