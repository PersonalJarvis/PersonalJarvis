import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentMemoryFiles, LearnedInstructions } from "./AgentKnowledge";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });

it("keeps memory readable while an older backend waits for the next app start", async () => {
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith("/knowledge")) return json({}, 404);
    if (url === "/api/wiki/tree") return json({ ok: true, folders: [
      { name: "society/scout", files: [{ slug: "memory", mtime: 1, size: 50 }] },
      { name: "society/scout-other", files: [{ slug: "private", mtime: 1, size: 50 }] },
    ] });
    return json({ path: "society/scout/memory.md", content: "Existing memory", updated_ms: 1 });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<QueryClientProvider client={new QueryClient()}><AgentMemoryFiles agentId="scout" sample={false} /></QueryClientProvider>);
  await screen.findByText("Existing memory");
  expect(screen.getByText("society.profile_card.runtime_pending")).toBeTruthy();
  expect(screen.queryByText("private.md")).toBeNull();
  expect(fetcher).toHaveBeenCalledWith("/api/society/memory/file?path=society%2Fscout%2Fmemory.md", expect.anything());
});

it("refreshes revised working instructions without presenting a pending review as completed", async () => {
  let corrected = false;
  vi.stubGlobal("fetch", vi.fn(async () => json({ files: [], learned_instructions: [corrected ? "Use dated sources." : "Check sources."], reviews: { pending: 2, done: 4 }, last_review: { state: "pending", updated_ms: 1000 } })));
  render(<QueryClientProvider client={new QueryClient()}><LearnedInstructions agentId="scout" sample={false} /></QueryClientProvider>);
  await screen.findByText("Check sources.");
  expect(screen.getByRole("status").textContent).toContain("review_pending");
  corrected = true;
  fireEvent.click(screen.getByRole("button", { name: "society.profile_card.refresh" }));
  await screen.findByText("Use dated sources.");
  expect(screen.queryByText("Check sources.")).toBeNull();
});
