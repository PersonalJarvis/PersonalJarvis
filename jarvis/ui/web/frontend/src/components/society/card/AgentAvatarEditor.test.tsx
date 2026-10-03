import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { SAMPLE_ROSTER } from "../mockRoster";
import { AgentAvatarEditor } from "./AgentAvatarEditor";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("../figures/AgentFigureViewer", () => ({
  AgentFigureViewer: () => <div data-testid="figure-preview" />,
}));

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

function renderEditor(sample = false) {
  const fetcher = vi.fn(async () => json({ agent: {} }));
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const agent = {
    ...SAMPLE_ROSTER[1],
    agentId: "research",
    tier: "specialist" as const,
    figure: {
      contract: 1 as const,
      archetype: "biped" as const,
      base: "rogue",
      parts: {},
      style: "fantasy",
    },
  };
  render(
    <QueryClientProvider client={client}>
      <AgentAvatarEditor agent={agent} sample={sample} />
    </QueryClientProvider>,
  );
  return { fetcher };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("shows only public avatar styles and never offers the reserved spirit", () => {
  renderEditor();
  const style = screen.getByLabelText("society.create.style") as HTMLSelectElement;
  const values = Array.from(style.options).map((option) => option.value);
  expect(values).toContain("fantasy");
  expect(values).not.toContain("spirit");
  expect(values).not.toContain("custom");
});

it("patches only the avatar when a catalog base changes", async () => {
  const { fetcher } = renderEditor();
  fireEvent.change(screen.getByLabelText("society.create.base"), {
    target: { value: "knight" },
  });
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));

  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  const patch = fetcher.mock.calls.find(
    ([url, init]) =>
      String(url) === "/api/society/agents/research" && init?.method === "PATCH",
  );
  expect(patch).toBeTruthy();
  const body = JSON.parse(String(patch?.[1]?.body));
  expect(Object.keys(body)).toEqual(["avatar"]);
  expect(body.avatar.base).toBe("knight");
  expect(body.avatar.model).toBeUndefined();
});

it("keeps avatar changes disabled for sample rows", () => {
  const { fetcher } = renderEditor(true);
  expect((screen.getByLabelText("society.create.style") as HTMLSelectElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "society.card.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetcher).not.toHaveBeenCalled();
});
