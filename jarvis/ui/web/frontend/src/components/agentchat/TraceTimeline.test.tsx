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
  // Folded to one line that says what happened.
  const toggle = screen.getByRole("button", { name: /^Worked for 2\.4s.*Used the wiki.*1 failed/ });
  // The turn is not called failed because one call was refused.
  expect(screen.getByRole("status").textContent).toContain("Done");
  fireEvent.click(toggle);

  // The model's own words read as prose, Markdown and all.
  const thought = document.querySelector<HTMLElement>("[data-trace-entry='thought']")!;
  expect(within(thought).getByText("wiki").tagName).toBe("STRONG");
  // Each call is one line, with what came of it underneath.
  const [wiki, refused] = Array.from(document.querySelectorAll<HTMLElement>("[data-trace-entry='tool']"));
  expect(wiki.textContent).toContain("Searched the wikiUrlaub 2026");
  expect(wiki.textContent).toContain("49ms");
  expect(wiki.textContent).toContain("1 result");
  expect(refused.textContent).toContain("Blocked: a safety rule blocks this action");
  // The raw call is one tap away.
  fireEvent.click(within(wiki).getByRole("button"));
  expect(within(wiki).getByText("Input")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy" })).toBeTruthy();
});

it("draws nothing for a turn whose only step is the brain call", () => {
  render(<VoiceWorkTrace steps={[step({ id: "b", kind: "brain", labelKey: "thinking.step_brain", detail: "m" })]} durationMs={0} />);
  expect(screen.queryByTestId("conversation-work-fold")).toBeNull();
});
