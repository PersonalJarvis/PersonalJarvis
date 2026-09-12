import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LiveProfile } from "./LiveProfile";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => vi.unstubAllGlobals());

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
  render(
    <QueryClientProvider client={client}>
      <LiveProfile />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", { name: "live.save" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("live.thinking_model"), {
    target: { value: "chosen-model" },
  });
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
  client.clear();
});
