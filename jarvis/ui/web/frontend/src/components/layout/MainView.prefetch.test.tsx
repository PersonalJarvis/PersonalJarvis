import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, test, vi } from "vitest";

import { MainView } from "./MainView";
import { useEventStore } from "@/store/events";

const loads = vi.hoisted(() => [] as string[]);
vi.mock("@/views/ChatsSurface", () => ({ ChatsSurface: () => <div>home</div> }));
vi.mock("@/views/SettingsHubView", () => {
  loads.push("settings");
  return { SettingsHubDialog: () => null };
});
vi.mock("@/views/society/SocietyView", () => {
  loads.push("agents");
  return { SocietyView: () => <div>agents ready</div> };
});
vi.mock("@/views/WikiView", () => {
  loads.push("memory");
  return { WikiView: () => null };
});
vi.mock("@/views/PluginsDialog", () => {
  loads.push("plugins");
  return { PluginsDialog: () => null };
});
vi.mock("@/views/AgenticIdeView", () => {
  loads.push("ide");
  return { AgenticIdeView: () => null };
});
vi.mock("@/views/AutomationsView", () => {
  loads.push("tasks");
  return { AutomationsView: () => null };
});
vi.mock("@/views/SessionsView", () => {
  loads.push("sessions");
  return { SessionsView: () => null };
});
vi.mock("@/views/ClisHubView", () => {
  loads.push("clis");
  return { ClisHubView: () => null };
});
vi.mock("@/views/DocsView", () => {
  loads.push("docs");
  return { DocsView: () => null };
});
vi.mock("@/views/BoardView", () => {
  loads.push("board");
  return { BoardView: () => null };
});
vi.mock("@/views/RunInspectorView", () => {
  loads.push("runs");
  return { RunInspectorView: () => null };
});
vi.mock("@/views/VoiceHubView", () => {
  loads.push("voice");
  return { VoiceHubView: () => null };
});
vi.mock("@/views/VisualizationView", () => {
  loads.push("artifacts");
  return { VisualizationView: () => null };
});
vi.mock("@/views/MarketplaceView", () => {
  loads.push("marketplace");
  return { MarketplaceView: () => null };
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

test("an idle home window loads no unused sections or speculative data; navigation still loads its view", async () => {
  vi.useFakeTimers();
  useEventStore.setState({ activeSection: "chats", solo: false, detachedViews: [] });
  const client = new QueryClient();
  const prefetch = vi.spyOn(client, "prefetchQuery").mockResolvedValue(undefined);
  render(<QueryClientProvider client={client}><MainView /></QueryClientProvider>);

  // Give both idle-callback and timer implementations enough slots to drain
  // the old blanket warm-up, without measuring wall time on a busy host.
  for (let i = 0; i < 15; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_000);
      await vi.dynamicImportSettled();
    });
  }
  expect(loads).toEqual([]);
  expect(prefetch).not.toHaveBeenCalled();
  expect(screen.getByText("home")).toBeDefined();

  await act(async () => {
    useEventStore.getState().setActiveSection("agents");
    await vi.dynamicImportSettled();
  });
  expect(loads).toEqual(["agents"]);
  expect(screen.getByText("agents ready")).toBeDefined();
  client.clear();
}, 30_000);
