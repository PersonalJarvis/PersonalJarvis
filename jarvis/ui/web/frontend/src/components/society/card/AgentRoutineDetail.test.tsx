import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AgentRoutineDetail, routineRuns } from "./AgentRoutineDetail";
import type { LiveRoutine } from "../cardData";
import type { TaskDetail, TaskStep } from "@/views/automations/automationsModel";
import en from "@/i18n/locales/society/en.json";

vi.mock("@/i18n", () => ({
  useLocaleChunk: () => {},
  useT: () => (key: string) => key.split(".").reduce<unknown>((value, part) => (value as Record<string, unknown>)?.[part], en) ?? key,
}));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(multiple = false) {
  const trigger = { type: "calendar", local_time: "08:00", timezone: "Europe/Berlin", weekdays: [0, 2] };
  const member: LiveRoutine = { id: "r1", title: "[agent:Mail] Inbox", prompt: "Read mail.", trigger, schedule: "", state: "scheduled", dueMs: null, lastRunMs: null };
  const members = multiple ? [member, { ...member, id: "r2" }] : [member];
  const tasks = new Map(members.map((m) => [m.id, {
    ...m, trigger_type: "calendar", spec: { action: { kind: "agent", prompt: "Identity\nRoutine:\nRead mail." } }, steps: [],
  } as unknown as TaskDetail]));
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const id = url.split("/").at(-1)!;
    if (method === "PATCH" && url.startsWith("/api/tasks/")) tasks.get(id)!.state = JSON.parse(String(init?.body)).enabled ? "scheduled" : "paused";
    return { ok: true, status: 200, json: async () => tasks.get(id) ?? {} } as Response;
  });
  vi.stubGlobal("fetch", fetcher);
  const onClose = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <AgentRoutineDetail routine={{ ...member, members }} agentId="mail" onClose={onClose} />
  </QueryClientProvider>);
  return { fetcher, onClose };
}

test("loads the instruction and saves all timings under the original ids", async () => {
  const { fetcher } = setup(true);
  await waitFor(() => expect((screen.getByLabelText("Name") as HTMLButtonElement).disabled).toBe(false));
  expect((screen.getByLabelText("Instruction") as HTMLTextAreaElement).value).toBe("Read mail.");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Updated inbox" } });
  fireEvent.click(screen.getByText("Save"));
  await screen.findByText("Saved.");
  const writes = fetcher.mock.calls.filter(([url, init]) => url.includes("/routines/") && init?.method === "PATCH");
  expect(writes.map(([url]) => url)).toEqual(["/api/society/agents/mail/routines/r1", "/api/society/agents/mail/routines/r2"]);
  expect(JSON.parse(String(writes[0][1]?.body))).toMatchObject({ title: "Updated inbox", prompt: "Read mail.", schedule: { weekdays: [0, 2] } });
});

test("pauses every timing, tests once, and requires a delete confirmation", async () => {
  const { fetcher, onClose } = setup(true);
  await waitFor(() => expect((screen.getByRole("switch") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("switch"));
  await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false"));
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "PATCH")).toHaveLength(2);
  fireEvent.click(screen.getByText("Test run"));
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/run"))).toHaveLength(1));
  await waitFor(() => expect((screen.getByText("Delete") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Delete"));
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
  fireEvent.click(screen.getAllByText("Delete")[1]);
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "DELETE").map(([url]) => url)).toEqual(["/api/tasks/r2", "/api/tasks/r1"]);
});

test("schedule editing keeps calendar restrictions and adding uses the parent", async () => {
  const { fetcher } = setup();
  await waitFor(() => expect((screen.getByText("Add another schedule") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Add another schedule"));
  fireEvent.click(screen.getByText("Save"));
  await screen.findByText("Saved.");
  const post = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
  expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ parent_task_id: "r1", prompt: "Read mail.", schedule: { type: "every" } });
});

test("failed saves remain editable and expose the error", async () => {
  const { fetcher } = setup();
  await waitFor(() => expect((screen.getByLabelText("Name") as HTMLButtonElement).disabled).toBe(false));
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url, init) => init?.method === "PATCH" ? { ok: false, status: 409, json: async () => ({ detail: "Routine is running" }) } as Response : original(url, init));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Unsaved name" } });
  fireEvent.click(screen.getByText("Save"));
  expect((await screen.findByRole("alert")).textContent).toContain("Routine is running");
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Unsaved name");
});

test("history distinguishes success, failure, active and legacy executions", () => {
  const events = ["agent_result", "run_started", "agent_result", "run_completed", "run_started", "error", "run_started"];
  const steps = events.map((event, seq) => ({ seq, kind: "log", timestamp_ns: seq * 1e9, payload: { event, text: "Result" } } as TaskStep));
  expect(routineRuns("r1", steps).map((run) => run.status)).toEqual(["legacy", "completed", "failed", "running"]);
});


test("editing the time saves the new value without dropping weekdays", async () => {
  const { fetcher } = setup();
  await waitFor(() => expect((screen.getByLabelText("Name") as HTMLInputElement).disabled).toBe(false));
  const timing = screen.getAllByRole("button").find((el) => el.textContent?.includes("08:00"))!;
  fireEvent.click(timing);
  fireEvent.change(screen.getByLabelText("Time"), { target: { value: "09:30" } });
  fireEvent.click(screen.getByText("Save"));
  await screen.findByText("Saved.");
  const patch = fetcher.mock.calls.find(([url, init]) => url.includes("/routines/") && init?.method === "PATCH");
  expect(JSON.parse(String(patch?.[1]?.body)).schedule).toMatchObject({ local_time: "09:30", weekdays: [0, 2] });
});


test("removing a timing cancels it instead of deleting its history", async () => {
  const { fetcher } = setup(true);
  await waitFor(() => expect((screen.getByText("Remove schedule") as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByText("Remove schedule"));
  await waitFor(() => expect(fetcher.mock.calls.some(([url, init]) => url === "/api/tasks/r2/cancel" && init?.method === "POST")).toBe(true));
  expect(fetcher.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
});
