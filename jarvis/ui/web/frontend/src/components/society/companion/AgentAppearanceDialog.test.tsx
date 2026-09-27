import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AgentAppearanceDialog } from "./AgentAppearanceDialog";
import { SAMPLE_ROSTER } from "../mockRoster";
import { defaultCompanion } from "./appearance";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("./CompanionPreview", () => ({ CompanionPreview: () => <div data-testid="preview" /> }));
vi.mock("../figures/AgentFigureViewer", () => ({ AgentFigureViewer: () => <div data-testid="character-preview" /> }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(fail = false, legacy = false) {
  const original = SAMPLE_ROSTER.find(a => a.tier !== "lead")!;
  const figure = legacy ? { contract: 1 as const, archetype: "biped" as const, base: "rogue", parts: {} } : original.figure!;
  const agent = { ...original, agentId: "appearance-test", figure: { ...figure, companion: { ...defaultCompanion("test"), shape: "circle" as const } } };
  const close = vi.fn();
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ agent }), { status: fail ? 500 : 200 }));
  vi.stubGlobal("fetch", fetcher);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })}><AgentAppearanceDialog agent={agent} sample={false} onClose={close} /></QueryClientProvider>);
  return { agent, fetcher, close };
}

it("saves companion edits without overwriting character parts or other agent fields", async () => {
  const { agent, fetcher } = setup();
  fireEvent.click(screen.getByRole("button", { name: "society.companion.shapes.cloud" }));
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  const [, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
  const body = JSON.parse(init.body as string);
  expect(Object.keys(body)).toEqual(["avatar"]);
  expect(body.avatar.companion.shape).toBe("cloud");
  expect(body.avatar.parts).toEqual(agent.figure?.parts);
  expect(body.avatar.base).toBe(agent.figure?.base);
});

it("protects an unsaved companion after a rejected save", async () => {
  const { close } = setup(true);
  fireEvent.click(screen.getByRole("button", { name: "society.companion.shapes.cloud" }));
  fireEvent.click(screen.getByRole("button", { name: "society.card.save" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "society.companion.shapes.cloud" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getAllByRole("button", { name: "society.card.close" })[0]);
  expect(close).not.toHaveBeenCalled();
  expect(screen.getByText("society.profile_card.unsaved")).toBeTruthy();
});

it("keeps the companion choice while editing the character", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "society.companion.shapes.cloud" }));
  fireEvent.click(screen.getByRole("tab", { name: "society.companion.character" }));
  await screen.findByTestId("character-preview");
  fireEvent.click(screen.getByRole("tab", { name: "society.companion.companion" }));
  expect(screen.getByRole("button", { name: "society.companion.shapes.cloud" }).getAttribute("aria-pressed")).toBe("true");
});

it("keeps shape customization without size or following-distance sliders", () => {
  setup();
  expect(screen.queryByRole("slider")).toBeNull();
  expect(screen.queryByText("society.companion.size")).toBeNull();
  expect(screen.queryByText("society.companion.distance")).toBeNull();
  expect(screen.getByRole("button", { name: "society.companion.shapes.cloud" })).toBeTruthy();
});

it("finds the character's actual style for older recipes without style metadata", async () => {
  setup(false, true);
  fireEvent.click(screen.getByRole("tab", { name: "society.companion.character" }));
  await screen.findByTestId("character-preview");
  expect((screen.getByLabelText("society.create.style") as HTMLSelectElement).value).toBe("fantasy");
  expect((screen.getByLabelText("society.create.base") as HTMLSelectElement).value).toBe("rogue");
});
