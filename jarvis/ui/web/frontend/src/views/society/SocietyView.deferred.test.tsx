import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SocietyView } from "./SocietyView";

const loaded = vi.hoisted(() => ({ building: 0, creator: 0, world: 0 }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, useLocaleChunk: () => true }));
vi.mock("@/lib/mapFullscreen", () => ({ setMapFullscreen: async () => undefined }));
vi.mock("@/components/society/chat/useModelMenuData", () => ({ useModelMenuData: () => undefined }));
vi.mock("@/lib/societyChatGroups", () => ({ useSocietyChatGroups: () => ({ data: [] }) }));
vi.mock("@/components/society/chat/ChatGroupPanel", () => ({ ChatGroupPanel: () => null }));
vi.mock("@/components/society/chat/AgentChatPanel", () => ({ useSocietyChatStore: { getState: () => ({ disconnect: () => undefined }) } }));
vi.mock("@/components/society/data", () => ({ useSocietyRoster: () => ({ data: { sample: false,
  agents: [{ agentId: "lead", name: "Lead", tier: "lead", state: "idle" }] }, isLoading: false }) }));
vi.mock("@/components/society/roster/RosterRail", () => ({ RosterRail: () => null }));
vi.mock("@/components/society/card/AgentCardOverlay", () => ({ AgentCardOverlay: ({ onCreate }: { onCreate: () => void }) => (
  <><input aria-label="Existing chat draft" /><button onClick={onCreate}>Create agent</button></>
) }));
vi.mock("@/views/JarvisAgentsView", () => {
  loaded.world += 1;
  return { JarvisAgentsView: ({ onSelectPlace }: { onSelectPlace: (place: string) => void }) => <>
    <button onClick={() => onSelectPlace("foundry")}>Open foundry</button>
    <button onClick={() => onSelectPlace("plugins")}>Open docks</button>
  </> };
});
vi.mock("@/components/society/card/BuildingCardOverlay", () => {
  // Counts MODULE evaluation, not component mounts. An eager import fails
  // before this test's first render, even when a closed dialog renders null.
  loaded.building += 1;
  return { BuildingCardOverlay: ({ place, onClose }: { place: string; onClose: () => void }) =>
    <section role="dialog" aria-label="Building"><span>{place}</span><button onClick={onClose}>Close building</button></section> };
});
vi.mock("@/components/society/create/CreateAgentDialog", () => {
  loaded.creator += 1;
  return { CreateAgentDialog: ({ open, onClose }: { open: boolean; onClose: () => void }) => open
    ? <section role="dialog" aria-label="Creator"><button onClick={onClose}>Close creator</button></section> : null };
});
afterEach(cleanup);

it("never evaluates closed dialog modules; opens, closes and reopens them on their real actions", async () => {
  const client = new QueryClient();
  render(<QueryClientProvider client={client}><SocietyView /></QueryClientProvider>);
  expect(loaded).toEqual({ building: 0, creator: 0, world: 0 });
  const draft = screen.getByLabelText("Existing chat draft");
  fireEvent.change(draft, { target: { value: "Keep this draft" } });

  fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
  expect(await screen.findByRole("dialog", { name: "Creator" })).toBeTruthy();
  expect(loaded).toEqual({ building: 0, creator: 1, world: 0 });
  fireEvent.click(screen.getByRole("button", { name: "Close creator" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Create agent" }));
  expect(await screen.findByRole("dialog", { name: "Creator" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close creator" }));
  expect(screen.getByLabelText("Existing chat draft")).toBe(draft);
  expect((draft as HTMLInputElement).value).toBe("Keep this draft");

  fireEvent.click(screen.getByRole("tab", { name: "society.world.mode_map" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open foundry" }));
  expect(await screen.findByRole("dialog", { name: "Building" })).toBeTruthy();
  expect(screen.getByText("foundry")).toBeTruthy();
  expect(loaded).toEqual({ building: 1, creator: 1, world: 1 });
  fireEvent.click(screen.getByRole("button", { name: "Close building" }));
  fireEvent.click(screen.getByRole("button", { name: "Open docks" }));
  expect(await screen.findByRole("dialog", { name: "Building" })).toBeTruthy();
  expect(screen.getByText("plugins")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close building" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  client.clear();
});
