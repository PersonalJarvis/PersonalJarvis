import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { VoiceWorkTrace } from "./VoiceWorkTrace";
import type { ThinkingStep } from "@/lib/thinkingSteps";

afterEach(cleanup);

const step = (over: Partial<ThinkingStep>): ThinkingStep => ({
  id: "s", kind: "tool", labelKey: "thinking.step_tool", status: "done", startedTs: 1000, ...over,
});

it("reopens a stored voice turn as a Claude/Codex-style timeline", () => {
  const steps: ThinkingStep[] = [
    step({ id: "b", kind: "brain", labelKey: "thinking.step_brain", detail: "grok · grok-4.3", durationMs: 900 }),
    step({ id: "p", kind: "thought", labelKey: "thinking.step_thought", detail: "I'll check the **wiki** first." }),
    step({ id: "w", detail: "wiki-recall", args: { query: "Urlaub 2026" }, result: '{"pages": [{"title": "Urlaub"}]}', durationMs: 49 }),
    step({ id: "x", detail: "run-app-action", status: "error", denied: true, error: "blacklist: <tool-declared-block>" }),
  ];
  render(<VoiceWorkTrace steps={steps} durationMs={2400} />);
  // Nothing folds; the turn is not called failed because one call was refused.
  expect(screen.queryByTestId("conversation-work-fold")).toBeNull();
  expect(screen.getByRole("status").textContent).toContain("Done");

  // The model's own words read as prose, Markdown and all.
  const words = document.querySelector<HTMLElement>("[data-trace-entry='reasoning']")!;
  expect(within(words).getByText("wiki").tagName).toBe("STRONG");
  // No bullets, threads or rings — just the lines.
  expect(document.querySelector(".trace-dot, .trace-report-num")).toBeNull();
  // The stretch is one quiet line; it opens to its calls.
  fireEvent.click(screen.getByRole("button", { name: /^Searched the wiki, used Run app action$/ }));
  const [wiki, refused] = Array.from(document.querySelectorAll<HTMLElement>("[data-trace-entry='call']"));
  expect(wiki.textContent).toContain("Searched the wikiUrlaub 2026");
  expect(refused.textContent).toContain("Run app action");
  expect(refused.textContent).toContain("Blocked");
  // A call opens to what came back and why it did not run.
  fireEvent.click(within(wiki).getByRole("button"));
  expect(wiki.textContent).toContain("1 result");
  expect(within(wiki).getByText("Input")).toBeTruthy();
  fireEvent.click(within(refused).getByRole("button"));
  expect(refused.textContent).toContain("Blocked: a safety rule blocks this action");
});

it("draws nothing for a turn whose only step is the brain call", () => {
  render(<VoiceWorkTrace steps={[step({ id: "b", kind: "brain", labelKey: "thinking.step_brain", detail: "m" })]} durationMs={0} />);
  expect(screen.queryByTestId("conversation-work-fold")).toBeNull();
});

it("keeps a stretch at one line while its calls run, so the chat never jumps", async () => {
  const { WorkTrace } = await import("./WorkTrace");
  const call = (id: string, output: string | null) => ({
    kind: "tool" as const, callId: id, name: "Bash", input: { command: `step ${id}` }, output,
    isError: false, durationMs: output === null ? null : 100, approval: null, startedMs: 0,
  });
  const lines = () => document.querySelectorAll("[data-trace-entry='activity'] > *").length;
  const { rerender } = render(<WorkTrace status="running" startedMs={0} durationMs={null} blocks={[call("a", null)]} />);
  expect(lines()).toBe(1);
  rerender(<WorkTrace status="running" startedMs={0} durationMs={null} blocks={[call("a", "ok"), call("b", null)]} />);
  // The running call takes the stretch's one line; it is not added below it.
  expect(lines()).toBe(1);
  expect(document.querySelector(".trace-shimmer")?.textContent).toBe("step b");
  rerender(<WorkTrace status="running" startedMs={0} durationMs={null} blocks={[call("a", "ok"), call("b", "ok")]} />);
  expect(lines()).toBe(1);
  expect(screen.getByRole("button", { name: /^Ran commands/ })).toBeTruthy();
  // A failure says why on the same line.
  rerender(<WorkTrace status="running" startedMs={0} durationMs={null} blocks={[{ ...call("a", "Exit code 1"), isError: true }]} />);
  expect(lines()).toBe(1);
  expect(screen.getByRole("button", { name: /step a.*Failed: Exit code 1/ })).toBeTruthy();
});
