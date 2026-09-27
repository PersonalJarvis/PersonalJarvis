import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LiveProfile } from "./LiveProfile";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("requires an explicit thinking model and sends one coherent selection", async () => {
  const requests: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        requests.push({ path, body: JSON.parse(String(init.body)) });
        return { ok: true, json: async () => ({}) };
      }
      return {
        ok: true,
        json: async () =>
          path.endsWith("options")
            ? {
                models: [{ id: "chosen-model", label: "Chosen" }],
                voices: ["gleam"],
                efforts: ["medium"],
              }
            : {
                key_ready: true,
                agent_configured: true,
                profile: {
                  model: "gpt-live-1",
                  voice: "gleam",
                  backend_model: "",
                  reasoning_effort: "medium",
                  web_search: true,
                  instructions: "",
                  backend_instructions: "",
                  configured: false,
                },
              },
      };
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(["unrelated-background-work"], { ready: true });
  const onSaved = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <LiveProfile onSaved={onSaved} />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", { name: "live.save" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Chosen/ }));
  fireEvent.click(button);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0]).toMatchObject({
    path: "/api/live/profile",
    body: {
      model: "gpt-live-1",
      backend_model: "chosen-model",
      configured: true,
      reasoning_effort: "medium",
      web_search: true,
    },
  });
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(client.getQueryState(["unrelated-background-work"])?.isInvalidated).toBe(false);
  client.clear();
});

it("recognizes a newly connected key without losing the user's model choice", async () => {
  let keyReady = false;
  const fetcher = vi.fn(async (path: string) => ({
    ok: true,
    json: async () => path.endsWith("options") ? {
      models: [{ id: "chosen-model", label: "Chosen" }], voices: ["gleam"], efforts: ["medium"],
    } : {
      key_ready: keyReady, active: false, agent_configured: true,
      profile: { model: "gpt-live-1", voice: "gleam", backend_model: "", reasoning_effort: "medium", web_search: true, instructions: "", backend_instructions: "", configured: false },
    },
  }));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><LiveProfile /></QueryClientProvider>);
  const button = await screen.findByRole("button", { name: "live.save" });
  fireEvent.click(screen.getByRole("combobox", { name: "live.thinking_model" }));
  fireEvent.click(await screen.findByRole("option", { name: /Chosen/ }));
  expect((button as HTMLButtonElement).disabled).toBe(true);
  keyReady = true;
  fireEvent(window, new CustomEvent("jarvis:secret-configured"));
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getByRole("combobox", { name: "live.thinking_model" }).textContent).toContain("Chosen");
  client.clear();
});
