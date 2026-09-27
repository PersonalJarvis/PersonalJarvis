import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SAMPLE_ROSTER } from "../mockRoster";
import { AgentProfileDialog } from "./AgentProfileDialog";
import { RosterRail } from "../roster/RosterRail";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("../AgentSwatch", () => ({ AgentSwatch: () => <span /> }));

const agent = { ...SAMPLE_ROSTER[1], agentId: "research", name: "Research", title: "Researcher", description: "Check primary sources.", tier: "specialist" as const };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const knowledge = { files: [
  { path: "memory/memory.md", name: "memory.md", kind: "memory", updated_ms: 1, size: 50 },
  { path: "memory/notes.md", name: "notes.md", kind: "memory", updated_ms: 2, size: 50 },
  { path: "skills/check/SKILL.md", name: "check/SKILL.md", kind: "skills", updated_ms: 3, size: 50 },
], learned_instructions: ["Check source dates."], reviews: { pending: 0, done: 1 }, last_review: { state: "done", updated_ms: 1000 } };

function setup(options: { lead?: boolean; sample?: boolean; fetcher?: typeof fetch; knowledgeFailure?: boolean } = {}) {
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (String(url).endsWith("/knowledge")) return json(knowledge, options.knowledgeFailure ? 500 : 200);
    return options.fetcher ? options.fetcher(url, init) : json({});
  });
  vi.stubGlobal("fetch", fetcher);
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><AgentProfileDialog agent={options.lead ? { ...agent, agentId: "jarvis", tier: "lead" } : agent} sample={options.sample ?? false} onClose={onClose} /></QueryClientProvider>);
  return { onClose, fetcher };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("saves the role and instructions without overwriting model, grants or approval rules", async () => {
  const { fetcher } = setup();
  fireEvent.change(screen.getByLabelText("society.profile_card.role"), { target: { value: "Source researcher" } });
  fireEvent.change(screen.getByLabelText("society.profile_card.instructions"), { target: { value: "Check dates too." } });
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));
  await screen.findByText("society.profile_card.saved");
  expect(fetcher).toHaveBeenCalledWith("/api/society/agents/research", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ title: "Source researcher", description: "Check dates too." }) }));
});

it("keeps failed edits and protects them from accidental dismissal", async () => {
  const { onClose } = setup({ fetcher: async () => json({}, 500) });
  fireEvent.change(screen.getByLabelText("society.profile_card.instructions"), { target: { value: "Unsaved instructions" } });
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));
  await screen.findByText("society.profile_card.save_error");
  expect((screen.getByLabelText("society.profile_card.instructions") as HTMLTextAreaElement).value).toBe("Unsaved instructions");
  fireEvent.click(screen.getAllByRole("button", { name: "society.card.close" })[0]);
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByText("society.profile_card.unsaved")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.profile_card.keep_editing" }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "society.profile_card.discard" }));
  expect(onClose).toHaveBeenCalledOnce();
});

it("uses the shared instructions endpoint for the lead", async () => {
  const { fetcher } = setup({ lead: true, fetcher: async (_url, init) => json({ content: init?.method === "PUT" ? "New shared rule" : "Shared rule", filename: "AGENTS.md" }) });
  await screen.findByDisplayValue("Shared rule");
  fireEvent.change(screen.getByLabelText("society.profile_card.instructions"), { target: { value: "New shared rule" } });
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));
  await screen.findByText("society.profile_card.saved");
  expect(fetcher).toHaveBeenCalledWith("/api/settings/agent-instructions", expect.objectContaining({ method: "PUT", body: JSON.stringify({ content: "New shared rule" }) }));
  expect(fetcher.mock.calls.some(([url, init]) => String(url).includes("/agents/jarvis") && init?.method === "PATCH")).toBe(false);
});

it("shows automatically learned instructions and review status separately from user instructions", async () => {
  setup();
  await screen.findByText("Check source dates.");
  expect(screen.getByText("society.profile_card.review_done")).toBeTruthy();
  expect((screen.getByLabelText("society.profile_card.instructions") as HTMLTextAreaElement).value).toBe(agent.description);
});

it("loads memory on demand, opens another file and filters filenames", async () => {
  const { fetcher } = setup({ fetcher: async (url) => json({ content: String(url).includes("notes.md") ? "Dated note" : "Durable memory" }) });
  expect(fetcher.mock.calls.every(([url]) => String(url).endsWith("/knowledge"))).toBe(true);
  fireEvent.mouseDown(screen.getByRole("tab", { name: "society.profile_card.memory" }), { button: 0, ctrlKey: false });
  await screen.findByText("Durable memory");
  fireEvent.click(screen.getByRole("button", { name: "notes.md society.profile_card.kind_memory" }));
  await screen.findByText("Dated note");
  expect(fetcher).toHaveBeenCalledWith("/api/society/agents/research/knowledge/file?path=memory%2Fnotes.md", expect.anything());
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "skills" } });
  expect(screen.queryByRole("button", { name: "notes.md society.profile_card.kind_memory" })).toBeNull();
  expect(screen.getByRole("button", { name: "check/SKILL.md society.profile_card.kind_skills" })).toBeTruthy();
});

it("reports listing failures instead of presenting an empty memory", async () => {
  setup({ knowledgeFailure: true });
  fireEvent.mouseDown(screen.getByRole("tab", { name: "society.profile_card.memory" }), { button: 0, ctrlKey: false });
  await screen.findByRole("alert");
  expect(screen.queryByText("society.profile_card.empty")).toBeNull();
});

it("does not read live data or save a sample agent", async () => {
  const { fetcher } = setup({ sample: true, lead: true });
  expect((screen.getByLabelText("society.profile_card.instructions") as HTMLTextAreaElement).disabled).toBe(true);
  fireEvent.mouseDown(screen.getByRole("tab", { name: "society.profile_card.memory" }), { button: 0, ctrlKey: false });
  expect(fetcher).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "society.card.save" }) as HTMLButtonElement).disabled).toBe(true);
});

it("opens the avatar profile without selecting the chat and leaves name-click navigation intact", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json(knowledge)));
  const onOpen = vi.fn();
  const client = new QueryClient();
  render(<QueryClientProvider client={client}><RosterRail agents={[agent]} sample={false} loading={false} activeAgentId={null} onOpen={onOpen} onCreate={() => undefined} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole("button", { name: "society.profile_card.open" }));
  await screen.findByRole("dialog");
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.click(screen.getAllByRole("button", { name: "society.card.close" })[0]);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: /Research Researcher/ }));
  expect(onOpen).toHaveBeenCalledWith("research");
});
