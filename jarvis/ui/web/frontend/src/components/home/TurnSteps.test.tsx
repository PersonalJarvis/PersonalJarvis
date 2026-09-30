import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, it } from "vitest";
import { TurnSteps, formatThoughtDuration, loadTurnTrace, traceWorthShowing } from "./TurnSteps";
import type { ThinkingStep } from "@/lib/thinkingSteps";
afterEach(cleanup);
// The trace renderer is a lazy chunk (see TurnSteps). Loading it once up front
// keeps a slow first transform out of the per-test wait; what it draws still
// arrives a tick after the render, so the assertions use findBy*.
beforeAll(async () => {
 await loadTurnTrace();
}, 30_000);
it("keeps brief voice errors visible", async () => {
 const steps: ThinkingStep[] = [{ id: "s", kind: "note", status: "error", labelKey: "thinking.step_update", error: "Connection lost", startedTs: 0 }];
 expect(traceWorthShowing(steps, 20, false)).toBe(true);
 render(<TurnSteps steps={steps} durationMs={20} />);
 expect(await screen.findByText("Connection lost")).toBeTruthy();
});
it("does not hide live work with no events", async () => {
 const { rerender, container } = render(<TurnSteps steps={[]} />);
 expect(container.textContent).toBe("");
 rerender(<TurnSteps steps={[]} live />);
 expect((await screen.findByRole("status")).textContent).toContain("Working");
});
it("preserves the public duration formatter", () => {
 expect(formatThoughtDuration(65000)).toBe("1m 05s");
});
