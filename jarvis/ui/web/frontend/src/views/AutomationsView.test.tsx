import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator: returns the key so assertions can match exact labels.
// The two strings with placeholders get a real template so the `fill` path
// (the humanized tool list, the added-notice name) is exercised.
const TEMPLATED: Record<string, string> = {
  "automations_view.needs": "Needs {tools}",
  "automations_view.added_notice": '"{name}" added.',
};

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => TEMPLATED[key] ?? key,
  useUiLanguage: () => "en",
  fill: (template: string, vars: Record<string, string | number>) =>
    template.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)),
}));

// The header comes from ChatsView, which drags the voice stage along — stub it.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title, right }: { title: string; right?: React.ReactNode }) => (
    <header>
      <h2>{title}</h2>
      {right}
    </header>
  ),
}));

// The create dialog has its own tests; here it only needs to be openable.
vi.mock("@/views/tasks/TaskCreateDialog", () => ({
  TaskCreateDialog: () => <div>CREATE_DIALOG</div>,
}));

import { AutomationsView } from "@/views/AutomationsView";

const NOW_NS = Date.now() * 1e6;

const TEMPLATES = {
  categories: ["news", "productivity"],
  templates: [
    {
      key: "morning_briefing",
      category: "news",
      icon: "sun",
      name: "Morning briefing",
      description: "Calendar, mail and the weather in one summary.",
      schedule: { kind: "daily", time: "07:30", weekday: 0 },
      schedule_label: "Daily at 07:30",
      plugin_grants: [{ plugin_id: "gmail", scope: "read" }],
      requires: ["gmail"],
      missing: [],
      ready: true,
      inputs: [
        { key: "city", label: "City", placeholder: "Berlin", default: "", required: true },
      ],
      model_tier: "auto",
      tags: [],
      prompt: "Brief me about {city}.",
    },
    {
      key: "inbox_triage",
      category: "productivity",
      icon: "mail",
      name: "Inbox triage",
      description: "Sort the inbox.",
      schedule: { kind: "hourly", time: "08:00", weekday: 0 },
      schedule_label: "Every hour",
      plugin_grants: [{ plugin_id: "gmail", scope: "write" }],
      requires: ["gmail"],
      missing: ["gmail"],
      ready: false,
      inputs: [],
      model_tier: "fast",
      tags: [],
      prompt: "Triage.",
    },
  ],
};

const AUTOMATION = {
  id: "auto1",
  title: "My inbox triage",
  state: "scheduled",
  trigger_type: "every",
  due_at_ns: NOW_NS + 3600e9,
  created_at_ns: NOW_NS - 100e9,
  started_at_ns: null,
  finished_at_ns: NOW_NS - 50e9,
  attempts: 1,
  last_error: null,
  tags: ["template:inbox_triage"],
  created_by: "template",
  interval_seconds: 3600,
  next_due_at_ns: NOW_NS + 3600e9,
  last_run_state: "completed",
  last_result: "Three mails need a reply.",
};

const RUN = {
  id: "run1",
  title: "Weekly report",
  state: "completed",
  trigger_type: "after_delay",
  due_at_ns: NOW_NS - 200e9,
  created_at_ns: NOW_NS - 300e9,
  started_at_ns: NOW_NS - 200e9,
  finished_at_ns: NOW_NS - 130e9,
  attempts: 1,
  last_error: null,
  tags: [],
  created_by: "user",
  interval_seconds: null,
  next_due_at_ns: null,
  last_run_state: "completed",
  last_result: "The report is done.",
};

interface Call {
  url: string;
  method: string;
  body?: unknown;
}

function installFetch(opts: { templatesStatus?: number; tasks?: unknown[] } = {}) {
  const calls: Call[] = [];
  const tasks = opts.tasks ?? [AUTOMATION, RUN];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const json = (status: number, payload: unknown) =>
      ({ ok: status < 400, status, json: async () => payload }) as Response;

    if (url.startsWith("/api/tasks/templates?")) {
      if (opts.templatesStatus === 404) return json(404, { detail: "Not Found" });
      return json(200, TEMPLATES);
    }
    if (url === "/api/tasks") return json(200, { tasks, total: tasks.length });
    if (url === "/api/tasks/templates/morning_briefing/add" && method === "POST") {
      return json(200, { id: "new1" });
    }
    if (url === "/api/tasks/auto1/run" && method === "POST") return json(200, { ok: true, id: "auto1" });
    if (url === "/api/tasks/auto1" && method === "PATCH") return json(200, { ok: true });
    if (url === "/api/tasks/auto1/cancel" && method === "POST") return json(200, { ok: true });
    if (url === "/api/tasks/auto1" && method === "DELETE") return json(200, { ok: true });
    if (url === "/api/tasks/run1") {
      return json(200, {
        ...RUN,
        spec: null,
        steps: [
          { seq: 1, kind: "start", payload: { event: "started" }, timestamp_ns: RUN.started_at_ns },
          {
            seq: 2,
            kind: "log",
            payload: { event: "agent_result", text: "The report is done.\nTwo items are open." },
            timestamp_ns: RUN.finished_at_ns,
          },
        ],
      });
    }
    if (url === "/api/tasks/auto1") return json(200, { ...AUTOMATION, spec: null, steps: [] });
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

function renderView() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AutomationsView />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AutomationsView", () => {
  it("renders the user's automations and the catalogue grouped by category", async () => {
    installFetch();
    renderView();
    // The user's card (from the template tag it borrows the template's description).
    const card = await screen.findByText("My inbox triage");
    expect(card).toBeTruthy();
    expect(screen.getByText("Three mails need a reply.")).toBeTruthy();
    // Catalogue groups in order with localized headings.
    expect(await screen.findByText("automations_view.category.news")).toBeTruthy();
    expect(screen.getByText("automations_view.category.productivity")).toBeTruthy();
    expect(screen.getByText("Morning briefing")).toBeTruthy();
    // The installed template says "Added"; the not-ready one names what it needs.
    const triage = screen.getByTestId("catalogue-inbox_triage");
    expect(within(triage).getByText("automations_view.added")).toBeTruthy();
    expect(within(triage).getByText("Needs Gmail")).toBeTruthy();
  });

  it("shows an honest note when the catalogue route does not exist yet", async () => {
    installFetch({ templatesStatus: 404 });
    renderView();
    expect(await screen.findByText("automations_view.catalogue_unavailable")).toBeTruthy();
    // The user's automations still render.
    expect(screen.getByText("My inbox triage")).toBeTruthy();
  });

  it("Add opens the dialog, validates required inputs and posts to the template route", async () => {
    const calls = installFetch();
    renderView();
    const briefing = await screen.findByTestId("catalogue-morning_briefing");
    fireEvent.click(within(briefing).getByText("automations_view.add"));
    const dialog = await screen.findByRole("dialog");
    // Required input empty → submit is refused and the field is flagged.
    fireEvent.click(within(dialog).getAllByText("automations_view.add").at(-1)!);
    expect(within(dialog).getByText("automations_view.required")).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/add"))).toBe(false);
    // Fill it in and submit.
    fireEvent.change(within(dialog).getByPlaceholderText("Berlin"), { target: { value: "Hamburg" } });
    fireEvent.click(within(dialog).getAllByText("automations_view.add").at(-1)!);
    await waitFor(() => {
      const add = calls.find((c) => c.url === "/api/tasks/templates/morning_briefing/add");
      expect(add?.method).toBe("POST");
      const body = add?.body as { inputs: Record<string, string>; schedule: { kind: string; time: string }; title: string; locale: string };
      expect(body.inputs).toEqual({ city: "Hamburg" });
      expect(body.schedule).toEqual({ kind: "daily", time: "07:30", weekday: 0 });
      expect(body.title).toBe("Morning briefing");
      expect(body.locale).toBe("en");
    });
    // Dialog closes, confirmation shows.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText('"Morning briefing" added.')).toBeTruthy();
  });

  it("the pause switch PATCHes {enabled:false} and Run now POSTs to /run", async () => {
    const calls = installFetch();
    renderView();
    await screen.findByText("My inbox triage");
    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() => {
      const patch = calls.find((c) => c.url === "/api/tasks/auto1" && c.method === "PATCH");
      expect(patch?.body).toEqual({ enabled: false });
    });
    fireEvent.click(screen.getByText("automations_view.run_now"));
    await waitFor(() => {
      expect(calls.some((c) => c.url === "/api/tasks/auto1/run" && c.method === "POST")).toBe(true);
    });
    expect(await screen.findByText("automations_view.run_started")).toBeTruthy();
  });

  it("Delete asks once, then cancels the active task and deletes it", async () => {
    const calls = installFetch();
    renderView();
    await screen.findByText("My inbox triage");
    fireEvent.click(screen.getByLabelText("tasks_view.delete"));
    expect(screen.getByText("automations_view.delete_confirm")).toBeTruthy();
    fireEvent.click(screen.getByText("tasks_view.delete"));
    await waitFor(() => {
      const idx = (m: string, u: string) => calls.findIndex((c) => c.url === u && c.method === m);
      const cancel = idx("POST", "/api/tasks/auto1/cancel");
      const del = idx("DELETE", "/api/tasks/auto1");
      expect(cancel).toBeGreaterThanOrEqual(0);
      expect(del).toBeGreaterThan(cancel);
    });
  });

  it("the Runs tab lists finished runs and expands to the readable result text", async () => {
    installFetch();
    renderView();
    await screen.findByText("My inbox triage");
    fireEvent.click(screen.getByRole("tab", { name: /automations_view.tab_runs/ }));
    expect(await screen.findByText("Weekly report")).toBeTruthy();
    // The recurring automation is not a "run" row unless it is running/finished.
    expect(screen.queryByText("My inbox triage")).toBeNull();
    fireEvent.click(screen.getByLabelText("tasks_view.expand"));
    const result = await screen.findByTestId("run-result");
    expect(result.textContent).toContain("The report is done.");
    expect(result.textContent).toContain("Two items are open.");
    expect(result.textContent).not.toContain("agent_result");
    // Filter chips: "problems" hides the completed run.
    fireEvent.click(screen.getByText("automations_view.runs_filter.problems"));
    expect(screen.queryByText("Weekly report")).toBeNull();
  });
});
