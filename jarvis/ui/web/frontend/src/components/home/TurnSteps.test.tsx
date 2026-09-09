import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { TurnSteps, formatThoughtDuration, traceWorthShowing } from "./TurnSteps";
import type { ThinkingStep } from "@/lib/thinkingSteps";
afterEach(cleanup);
it("keeps brief voice errors visible", () => {
 const steps: ThinkingStep[] = [{ id: "s", kind: "note", status: "error", labelKey: "thinking.step_update", error: "Connection lost", startedTs: 0 }];
 expect(traceWorthShowing(steps, 20, false)).toBe(true);
 render(<TurnSteps steps={steps} durationMs={20} />);
 expect(screen.getByText("Connection lost")).toBeTruthy();
});
it("does not hide live work with no events", () => {
 const { rerender, container } = render(<TurnSteps steps={[]} />);
 expect(container.textContent).toBe("");
 rerender(<TurnSteps steps={[]} live />);
 expect(screen.getByRole("status").textContent).toContain("Working");
});
it("preserves the public duration formatter", () => {
 expect(formatThoughtDuration(65000)).toBe("1m 05s");
});
