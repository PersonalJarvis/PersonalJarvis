import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useSocietyCapabilities } from "./data";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("refreshes installed, disabled and removed entries whenever @ is reopened", async () => {
  let ids = ["skill:old"];
  const fetcher = vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes("marketplace")
    ? { plugins: [{ id: "community", display_name: "Community", description: "Tasks" }] }
    : { capabilities: ids.map((id) => ({ id })) } }));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(({ open }) => useSocietyCapabilities(true, open), { initialProps: { open: false }, wrapper });
  await waitFor(() => expect(hook.result.current.data?.[0].id).toBe("skill:old"));
  ids = ["skill:new"];
  hook.rerender({ open: true });
  await waitFor(() => expect(hook.result.current.data?.[0].id).toBe("skill:new"));
  await waitFor(() => expect(hook.result.current.plugins[0].id).toBe("community"));
  hook.rerender({ open: false });
  ids = [];
  hook.rerender({ open: true });
  await waitFor(() => expect(hook.result.current.data).toEqual([]));
  expect(fetcher).toHaveBeenCalledWith("/api/society/capabilities", { cache: "no-store" });
  hook.unmount();
  client.clear();
});

it("reports inventory failures and recovers with the retry action", async () => {
  let failed = true;
  vi.stubGlobal("fetch", async () => ({ ok: !failed, status: 503, json: async () => ({ capabilities: [], plugins: [] }) }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const hook = renderHook(() => useSocietyCapabilities(true, true), { wrapper });
  await waitFor(() => expect(hook.result.current.inventoryError).toBe(true));
  failed = false;
  act(() => hook.result.current.retryInventory());
  await waitFor(() => expect(hook.result.current.inventoryError).toBe(false));
  expect(hook.result.current.data).toEqual([]);
  hook.unmount();
  client.clear();
});
