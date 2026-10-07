import { act, cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MainView } from "./MainView";
import { useEventStore } from "@/store/events";

const loads = vi.hoisted(() => ({ agents: 0, settings: 0 }));
vi.mock("@/views/ChatsSurface", () => ({ ChatsSurface: () => <div>Current chat</div> }));
vi.mock("@/views/society/SocietyView", () => {
  loads.agents += 1;
  return { SocietyView: () => <div>Requested agents</div> };
});
vi.mock("@/views/SettingsHubView", () => {
  loads.settings += 1;
  return { SettingsHubDialog: () => <div>Settings</div> };
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("does not execute unused sections or warm API queries while idle, then loads the requested section", async () => {
  vi.useFakeTimers();
  const idle = vi.fn((callback: () => void) => window.setTimeout(callback, 1));
  vi.stubGlobal("requestIdleCallback", idle);
  vi.stubGlobal("cancelIdleCallback", window.clearTimeout);
  const client = new QueryClient();
  const prefetch = vi.spyOn(client, "prefetchQuery");
  useEventStore.setState({ activeSection: "chats", solo: false, detachedViews: [] });
  render(<QueryClientProvider client={client}><MainView /></QueryClientProvider>);
  expect(screen.getByText("Current chat")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(loads).toEqual({ agents: 0, settings: 0 });
  expect(prefetch).not.toHaveBeenCalled();
  expect(idle).not.toHaveBeenCalled();

  vi.useRealTimers();
  act(() => useEventStore.getState().setActiveSection("agents"));
  expect(await screen.findByText("Requested agents")).toBeTruthy();
  expect(loads.agents).toBe(1);
  expect(loads.settings).toBe(0);
  client.clear();
});
