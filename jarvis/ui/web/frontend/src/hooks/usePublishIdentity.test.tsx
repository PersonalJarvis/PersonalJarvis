import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usePublishIdentity } from "./usePublishIdentity";

const modules = vi.hoisted(() => ({ publishingUi: 0 }));
vi.mock("@/components/marketplace/PublishIdentity", () => {
  modules.publishingUi += 1;
  return {};
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Consumer({ label }: { label: string }) {
  const identity = usePublishIdentity();
  return <span aria-label={label}>{identity.data?.login ?? "Pending"}</span>;
}

it("shares the identity read without evaluating the optional publishing UI", async () => {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ enabled: true, signed_in: true, login: "sample-publisher" }), { status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><Consumer label="Sidebar identity" /><Consumer label="Other identity" /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("Sidebar identity").textContent).toBe("sample-publisher"));
  expect(screen.getByLabelText("Other identity").textContent).toBe("sample-publisher");
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher).toHaveBeenCalledWith("/api/marketplace/publish/identity", { cache: "no-store" });
  expect(modules.publishingUi).toBe(0);
  view.unmount();
  client.clear();
});
