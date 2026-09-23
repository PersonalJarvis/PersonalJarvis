import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ReactNode } from "react";
import type { AgentChatCatalog } from "@/lib/agentChatApi";
import { useModelMenuData } from "./useModelMenuData";
import { clearModelMenuSnapshot } from "./modelMenuSnapshot";

afterEach(() => { cleanup(); clearModelMenuSnapshot(); vi.unstubAllGlobals(); });

test("changing a pinned account hides the previous models until that account replies", async () => {
  clearModelMenuSnapshot();
  let release!: () => void;
  const secondAccount = new Promise<void>((resolve) => { release = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.includes("/catalog")) {
      const account = new URL(url, "http://localhost").searchParams.get("account_id");
      if (account === "second") await secondAccount;
      const model = account ? `${account}-only` : "default-account-only";
      return new Response(JSON.stringify({ providers: [{
        id: "openai-codex", label: "Codex", family: "openai", runner: "codex-cli",
        cli_installed: true, models_source: "curated", curated_models: [{ id: model, label: model }],
        keyless: false, native_resume: false,
        default_model: "", effort_levels: [], default_effort: "", permission_modes: [], default_permission_mode: "plan",
      }], default_cwd: "", shell: "" } satisfies Partial<AgentChatCatalog>));
    }
    if (url.includes("/jarvis-agent/status")) return new Response(JSON.stringify({ mapping: [] }));
    return new Response(JSON.stringify({ providers: [] }));
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result, rerender } = renderHook(({ account }) => useModelMenuData(null, [], { "openai-codex": account }), {
    wrapper, initialProps: { account: "first" },
  });
  const models = () => result.current.options.find((row) => row.id === "openai-codex")?.curated_models.map((row) => row.id);
  await waitFor(() => expect(models()).toEqual(["first-only"]));
  rerender({ account: "second" });
  expect(models()).toEqual([]);
  expect(result.current.loading).toBe(true);
  await act(async () => { release(); await secondAccount; });
  await waitFor(() => expect(models()).toEqual(["second-only"]));
  client.clear();
});
