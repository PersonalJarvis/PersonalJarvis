import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AgentChatCatalog, AgentChatProvider, AgentConnectionRow } from "@/lib/agentChatApi";
import { CreateAgentDialog } from "./CreateAgentDialog";

const mutation = vi.hoisted(() => ({ create: vi.fn(async (_input: unknown) => ({ agentId: "created-agent" })) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("../data", () => ({ useCreateAgent: () => mutation.create }));
// Keep the real form, query cache, provider join and model pickers. Neither
// rendering WebGL nor looking up another computer is part of default selection.
vi.mock("../figures/AgentFigureViewer", () => ({ AgentFigureViewer: () => null }));
vi.mock("../companion/CompanionEditor", () => ({ CompanionEditor: () => null }));
vi.mock("./ComputerPicker", () => ({ ComputerPicker: () => null }));
vi.mock("@/components/agentchat/AgentComposer", () => ({ effortLabel: (value: string) => value }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const subscription: AgentChatProvider = {
  id: "sample-cli", label: "Subscription seat", family: "openai", runner: "codex-cli",
  models_source: "curated", curated_models: [{ id: "warm-model", label: "Warm model" }],
  default_model: "warm-model", keyless: false, native_resume: true,
  effort_levels: ["low", "high"], default_effort: "high",
  permission_modes: [], default_permission_mode: "ask", cli_installed: true,
};
function warmClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const catalog: AgentChatCatalog = { providers: [subscription], default_cwd: "", shell: "" };
  const connections: AgentConnectionRow[] = [{ jarvis: "sample-cli", key_set: true, is_active_brain: false }];
  client.setQueryData(["agent-chat", "catalog", "society"], catalog);
  client.setQueryData(["agent-chat", "connections"], connections);
  client.setQueryData(["society", "providers"], []);
  client.setQueryData(["agent-chat", "live-models", "keyless", []], {});
  return client;
}
function Harness() {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>Open creator</button>
    {open && <CreateAgentDialog open onClose={() => setOpen(false)} onCreated={() => setOpen(false)} />}
  </>;
}
async function expectDefaultSeat() {
  await waitFor(() => expect(screen.getByTestId("society-create-provider").textContent).toContain("Subscription seat"));
  expect(screen.getByRole("combobox", { name: "society.create.model" }).textContent).toContain("Warm model");
}

it("selects and submits the cached default on first mount and every fresh reopening", async () => {
  const client = warmClient();
  const view = render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  for (const name of ["First agent", "Reopened agent"]) {
    fireEvent.click(screen.getByRole("button", { name: "Open creator" }));
    await expectDefaultSeat();
    const input = screen.getByLabelText("society.create.name");
    expect((input as HTMLInputElement).value).toBe("");
    fireEvent.change(input, { target: { value: name } });
    fireEvent.click(screen.getByTestId("society-create-submit"));
    await waitFor(() => expect(screen.queryByTestId("create-agent-dialog")).toBeNull());
    expect(mutation.create).toHaveBeenLastCalledWith(expect.objectContaining({ name, provider: "sample-cli", model: "warm-model", effort: "high" }));
  }
  expect(mutation.create).toHaveBeenCalledTimes(2);
  view.unmount();
  client.clear();
});

it("does not leave an empty provider after cancelling and reopening a warm creator", async () => {
  const client = warmClient();
  const view = render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "Open creator" }));
  await expectDefaultSeat();
  fireEvent.change(screen.getByLabelText("society.create.name"), { target: { value: "Cancelled draft" } });
  fireEvent.click(screen.getByRole("button", { name: "society.create.cancel" }));
  fireEvent.click(screen.getByRole("button", { name: "Open creator" }));
  await expectDefaultSeat();
  expect((screen.getByLabelText("society.create.name") as HTMLInputElement).value).toBe("");
  expect(mutation.create).not.toHaveBeenCalled();
  view.unmount();
  client.clear();
});
