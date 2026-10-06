import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { JarvisActionsView } from "./JarvisActionsView";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
  fill: (template: string, vars: Record<string, string | number>) =>
    template.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)),
}));

const now = Date.now() / 1000;
const action = (id: string, area: string, title: string, method = "GET") => ({
  id,
  method,
  path: `/api/${id}`,
  area,
  title,
  description: "",
  dangerous: false,
  default_tier: method === "GET" ? "safe" : "monitor",
  mode: null,
  tier: method === "GET" ? "safe" : "monitor",
});
const catalog = {
  actions: [
    action("list_tasks", "tasks", "List tasks"),
    action("rename_pane", "agentic-ide", "Rename a pane", "POST"),
  ],
  areas: ["agentic-ide", "tasks"],
  count: 2,
};
const row = (at: number, outcome = "ran") => ({
  action: "tasks-list",
  title: "List tasks",
  description: "",
  area: "tasks",
  kind: "read",
  catalog_id: "list_tasks",
  outcome,
  detail: "",
  via: "app-command",
  at,
});

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      new Response(
        JSON.stringify(
          url.includes("/history")
            ? { history: [row(now), row(now - 5), row(now - 10, "failed")] }
            : catalog,
        ),
      ),
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <JarvisActionsView />
    </QueryClientProvider>,
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("names history rows, folds repeats and opens the action's permission", async () => {
  const timeline = await screen.findByTestId("jarvis-actions-history");
  expect(await within(timeline).findByText("×2")).toBeTruthy();
  expect(within(timeline).getAllByText("List tasks")).toHaveLength(2);
  expect(within(timeline).queryByText("tasks-list")).toBeNull();

  fireEvent.click(within(timeline).getAllByTitle("jarvis_actions.open_permission")[0]);
  expect(screen.getAllByTestId("jarvis-action-row")).toHaveLength(1);
  fireEvent.click(screen.getByText("jarvis_actions.focus_clear"));
  expect(screen.getAllByTestId("jarvis-action-row")).toHaveLength(2);
});

it("groups permissions by a readable area and filters by tier", async () => {
  expect(await screen.findByText("Agentic IDE")).toBeTruthy();
  const overview = screen.getByTestId("jarvis-actions-overview");
  fireEvent.click(within(overview).getByText("jarvis_actions.group_monitor").closest("button")!);
  const rows = screen.getAllByTestId("jarvis-action-row");
  expect(rows).toHaveLength(1);
  expect(within(rows[0]).getByText("Rename a pane")).toBeTruthy();
});
