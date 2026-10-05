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

function renderEditor(sample = false, sharedFigures: unknown[] = []) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input) === "/api/society/figures/share") {
      return json({ figures: sharedFigures, total: sharedFigures.length });
    }
    return json({ agent: {} });
  });
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

it("shows only public avatar styles and never offers the reserved spirit", async () => {
  renderEditor();
  fireEvent.click(screen.getByRole("combobox", { name: "society.create.style" }));
  expect(
    await screen.findByRole("option", { name: "society.style.fantasy" }),
  ).toBeTruthy();
  expect(screen.queryByRole("option", { name: "society.style.spirit" })).toBeNull();
  expect(screen.queryByRole("option", { name: "society.style.custom" })).toBeNull();
});

it("patches only the avatar when a catalog base changes", async () => {
  const { fetcher } = renderEditor();
  fireEvent.click(screen.getByRole("combobox", { name: "society.create.base" }));
  const options = await screen.findAllByRole("option");
  const knight = options.find((option) => option.getAttribute("data-value") === "knight");
  expect(knight).toBeTruthy();
  fireEvent.click(knight!);
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

it("applies a shared catalog recipe without introducing a model URL", async () => {
  const shared = [{
    id: "shared-rogue",
    name: "Olive Scout",
    license: "CC0-1.0",
    source: "reviewed recipe",
    report_count: 0,
    recipe: {
      contract: 1,
      archetype: "biped",
      base: "rogue",
      parts: {},
      palette: { primary: "#315d45" },
      style: "fantasy",
    },
  }];
  const { fetcher } = renderEditor(false, shared);

  fireEvent.click(await screen.findByRole("button", { name: /Olive Scout/ }));
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));

  await waitFor(() => {
    expect(fetcher.mock.calls.some(
      ([url, init]) => String(url) === "/api/society/agents/research" && init?.method === "PATCH",
    )).toBe(true);
  });
  const patch = fetcher.mock.calls.find(
    ([url, init]) =>
      String(url) === "/api/society/agents/research" && init?.method === "PATCH",
  );
  const body = JSON.parse(String(patch?.[1]?.body));
  expect(body.avatar.base).toBe("rogue");
  expect(body.avatar.palette.primary).toBe("#315d45");
  expect(body.avatar.model).toBeUndefined();
});

it("keeps avatar changes disabled for sample rows", () => {
  const { fetcher } = renderEditor(true);
  expect(
    (screen.getByRole("combobox", { name: "society.create.style" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect((screen.getByRole("button", { name: "society.card.save" }) as HTMLButtonElement).disabled).toBe(true);
  expect(fetcher).not.toHaveBeenCalled();
});
