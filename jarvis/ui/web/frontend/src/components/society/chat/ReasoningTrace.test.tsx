import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { ReasoningTrace } from "@/components/agentchat/WorkTrace";
import type { ReasoningBlock } from "@/components/agentchat/reduce";
const block: ReasoningBlock = { kind: "reasoning", id: "r", text: "**Inspect** the files.", durationMs: 8000, live: false, startedMs: 0 };
afterEach(cleanup);
it("keeps the thought open during work and folds on completion", () => {
 const { rerender } = render(<ReasoningTrace block={block} turnLive />);
 expect(screen.getByText("Inspect").tagName).toBe("STRONG");
 rerender(<ReasoningTrace block={block} turnLive={false} />);
 const button = screen.getByRole("button", { name: "Thought for 8.0s" });
 expect(button.getAttribute("aria-expanded")).toBe("false");
 fireEvent.click(button);
 expect(screen.getByText("Inspect").tagName).toBe("STRONG");
});
it("does not offer an empty disclosure for redacted thought", () => {
 render(<ReasoningTrace block={{...block, text: ""}} turnLive={false} />);
 expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
});
