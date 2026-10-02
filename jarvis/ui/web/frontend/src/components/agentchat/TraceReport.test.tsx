import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { VoiceWorkTrace } from "./VoiceWorkTrace";
import type { ThinkingStep } from "@/lib/thinkingSteps";

afterEach(cleanup);

const step = (over: Partial<ThinkingStep>): ThinkingStep => ({
  id: "s", kind: "tool", labelKey: "thinking.step_tool", status: "done", startedTs: 1000, ...over,
});

it("reopens a stored voice turn as a written report, not as rows", () => {
  const steps: ThinkingStep[] = [
    step({ id: "b", kind: "brain", labelKey: "thinking.step_brain", detail: "grok · grok-4.3", durationMs: 900 }),
    step({ id: "p", kind: "thought", labelKey: "thinking.step_thought", detail: "I'll check the **wiki** first." }),
    step({ id: "w", detail: "wiki-recall", args: { query: "Urlaub 2026" }, result: '{"pages": [{"title": "Urlaub"}]}', durationMs: 49 }),
    step({ id: "x", detail: "run-app-action", status: "error", denied: true, error: "blacklist: <tool-declared-block>" }),
  ];
  render(<VoiceWorkTrace steps={steps} durationMs={2400} />);
  // Folded: the toggle names the time, the step count and the trouble.
  const toggle = screen.getByRole("button", { name: /^Thought for 2\.4s.*2 steps.*1 not done/ });
  expect(screen.queryByTestId("trace-report")).toBeNull();
  // The turn is not called failed because one step was refused.
  expect(screen.getByRole("status").textContent).toContain("Done");
  fireEvent.click(toggle);

  const report = screen.getByTestId("trace-report");
  expect(within(report).getByText(/This answer took 2\.4 s and 2 steps\./).textContent).toContain("Model: grok · grok-4.3.");
  const [wiki, refused] = Array.from(report.querySelectorAll<HTMLElement>("li[data-kind='action']"));
  // The reason in the model's words, rendered as Markdown, sits above its step.
  expect(within(wiki).getByText("wiki").tagName).toBe("STRONG");
  expect(wiki.textContent).toContain("Looked up “Urlaub 2026” in the wiki.");
  expect(wiki.textContent).toContain("49 ms");
  expect(wiki.textContent).toContain("It returned one entry in pages.");
  expect(refused.getAttribute("data-outcome")).toBe("denied");
  expect(refused.textContent).toContain("It did not run: a safety rule blocks this action.");
  // The raw call is one tap further.
  fireEvent.click(within(wiki).getByRole("button", { name: "Details" }));
  expect(within(wiki).getByText("Input")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy as text" })).toBeTruthy();
});

it("shows no report for a turn with nothing to tell", () => {
  render(<VoiceWorkTrace steps={[step({ id: "b", kind: "brain", labelKey: "thinking.step_brain", detail: "m" })]} durationMs={0} />);
  expect(screen.queryByTestId("conversation-work-fold")).toBeNull();
});
