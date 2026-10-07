import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { TranscriptActions } from "./TranscriptActions";
import { transcriptFixture } from "./testFixtures";
import { useI18nStore } from "@/i18n";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("opens an export with the remembered native application exactly once", async () => {
  vi.useFakeTimers();
  useI18nStore.setState({ ui: "en" });
  const requests: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    requests.push({ url, body: init?.body as string | undefined });
    if (url === "/api/downloads/capabilities") return Response.json({ native_file_actions: true, platform: "win32" });
    if (url === "/api/outputs/openers") return Response.json({ openers: [{ id: "editor", label: "Text editor" }] });
    if (url === "/api/outputs/preferred-opener") return Response.json({ opener: "editor" });
    if (url.endsWith("/open-with?format=markdown")) return Response.json({ opened: true });
    throw new Error(`Unexpected request: ${url}`);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(<QueryClientProvider client={client}><TranscriptActions detail={transcriptFixture()} /></QueryClientProvider>);
  await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
  vi.useRealTimers();
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  fireEvent.click(screen.getByRole("combobox", { name: "File format" }));
  fireEvent.click(await screen.findByRole("option", { name: "Markdown" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open with…" }));
  await waitFor(() => expect(requests.filter((r) => r.url.includes("/open-with?"))).toEqual([
    { url: "/api/sessions/voice%2Fone/open-with?format=markdown", body: JSON.stringify({ opener: "editor" }) },
  ]));
  await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/opened/i));
  client.clear();
});
