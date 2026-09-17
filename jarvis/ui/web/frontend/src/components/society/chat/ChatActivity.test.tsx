import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, it } from "vitest";
import { loadLocaleChunk } from "@/i18n";
import { AgentMessageActivity, RoutineActivity, routineTask } from "./ChatActivity";
import { ReasoningTrace, WorkTrace } from "@/components/agentchat/WorkTrace";
import type { InternalMessageItem, ReasoningBlock, ToolBlock } from "@/components/agentchat/reduce";

beforeAll(() => loadLocaleChunk("society"));
afterEach(cleanup);

const envelope = "Scheduled routine task-123. Follow your CURRENT standing instructions and permissions.\nUse your memory and conversation archive for prior results. For information watches, check sources and dates, remember last-seen items, and report only meaningful new findings.\n\n";

it("folds only a full scheduler envelope, preserving ordinary user messages", () => {
  expect(routineTask(envelope + "Check the inbox.\nSummarize changes.")).toBe("Check the inbox.\nSummarize changes.");
  expect(routineTask("Scheduled routine task-123. Please explain this.")).toBeNull();
  expect(routineTask("Please quote:\n" + envelope)).toBeNull();
  render(<RoutineActivity task={"Check the inbox.\nSummarize changes."} original={envelope + "Check the inbox.\nSummarize changes."} />);
  expect(screen.queryByText("Summarize changes.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Routine check · Check the inbox." }));
  expect(screen.getAllByText(/Summarize changes\./, { selector: "p" }).length).toBeGreaterThan(0);
});

it("shows live reasoning, hides it as soon as thinking finishes, and permits reopening", () => {
  const block: ReasoningBlock = { kind: "reasoning", id: "r", text: "Inspect the inbox first.", live: true, startedMs: Date.now(), durationMs: null };
  const { rerender } = render(<ReasoningTrace block={block} turnLive compact />);
  expect(screen.getByText(block.text)).toBeTruthy();
  rerender(<ReasoningTrace block={{ ...block, live: false, durationMs: 3000 }} turnLive compact />);
  expect(screen.queryByText(block.text)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Thought for 3.0s" }));
  expect(screen.getByText(block.text)).toBeTruthy();
  rerender(<ReasoningTrace block={{ ...block, live: false, durationMs: 3000 }} turnLive={false} compact />);
  expect(screen.queryByText(block.text)).toBeNull();
});

it("folds a live manual choice on completion even when the provider never ends reasoning", () => {
  const block: ReasoningBlock = { kind: "reasoning", id: "r", text: "Inspect the inbox first.", live: true, startedMs: Date.now(), durationMs: null };
  const { rerender } = render(<ReasoningTrace block={block} turnLive compact />);
  rerender(<ReasoningTrace block={block} turnLive={false} compact />);
  expect(screen.queryByText(block.text)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Thought" }));
  expect(screen.getByText(block.text)).toBeTruthy();
});

it("keeps live conversation tool rows left-aligned instead of centering them", () => {
  const live: ToolBlock = {
    kind: "tool", callId: "live", name: "search_files", input: { pattern: "archive" },
    output: null, isError: false, durationMs: null, approval: null, startedMs: 0,
  };
  const { container } = render(<WorkTrace conversation status="running" startedMs={0} durationMs={null} blocks={[
    { kind: "reasoning", id: "r", text: "Inspect archive.", live: false, durationMs: 2000, startedMs: 0 },
    live,
  ]} />);
  // A live tool is a singleton group; mx-auto used to park it in the middle of
  // the lane, then snap it left once it joined the completed activity fold.
  const liveRow = container.querySelector("[data-trace-tool]")?.parentElement;
  expect(liveRow?.className ?? "").not.toMatch(/(^|\s)mx-auto(\s|$)/);
  expect(container.querySelectorAll(".mx-auto")).toHaveLength(0);
});

it("keeps a failure visible when the surrounding work is folded", () => {
  const tool: ToolBlock = { kind: "tool", callId: "a", name: "read_file", input: { path: "report.csv" }, output: "Report contents", isError: false, durationMs: 100, approval: null, startedMs: 0 };
  render(<WorkTrace conversation status="done" startedMs={0} durationMs={2000} blocks={[
    tool,
    { ...tool, callId: "error", isError: true, output: "Upload failed" },
    { kind: "text", id: "reply", text: "I could not finish." },
  ]} />);
  expect(screen.getByText("Upload failed")).toBeTruthy();
  expect(screen.getByText("I could not finish.")).toBeTruthy();
  expect(screen.queryByText("Report contents")).toBeNull();
  expect(screen.getByRole("button", { name: "Thought for 2.0s" })).toBeTruthy();
});

it("keeps failures and approvals visible in the conversation style", () => {
  const base: ToolBlock = { kind: "tool", callId: "failure", name: "send_message", input: {}, output: "Delivery failed", isError: true, durationMs: 100, approval: null, startedMs: 0 };
  render(<WorkTrace conversation status="done" startedMs={0} durationMs={1000} blocks={[
    base,
    { ...base, callId: "approval", isError: false, output: null, approval: { approvalId: "ap", summary: "Send this message?", decision: null } },
  ]} onDecide={() => undefined} />);
  expect(screen.getByText("Delivery failed")).toBeTruthy();
  expect(screen.getByText("Send this message?")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
});

it("shows agent message direction and truthful delivery state without preview clutter", () => {
  const item: InternalMessageItem = { type: "internal", id: "m", tsMs: 0, outgoing: { recipientId: "scout", recipientName: "Scout" }, message: { message_id: "m", sender_id: "lead", sender_name: "Lead", sender_kind: "agent", text: "Please check the report.", prompt: "", trace_id: "trace", turn_id: "turn", status: "queued", error: "" } };
  const { rerender } = render(<AgentMessageActivity item={item} roster={[]} />);
  expect(screen.getByRole("button", { name: /Message to Scout.*Queued/ })).toBeTruthy();
  expect(screen.queryByText(item.message.text)).toBeNull();
  fireEvent.click(screen.getByRole("button"));
  expect(screen.getByText(item.message.text)).toBeTruthy();
  rerender(<AgentMessageActivity item={{ ...item, message: { ...item.message, status: "failed", error: "Connection closed" } }} roster={[]} />);
  expect(screen.getByRole("button", { name: /Message failed Scout/ })).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toBe("Connection closed");
});

it("folds successful work between replies without swallowing an error or reordering the conversation", () => {
  const tool: ToolBlock = { kind: "tool", callId: "a", name: "read_file", input: { path: "report.csv" }, output: "Report contents", isError: false, durationMs: 100, approval: null, startedMs: 0 };
  const blocks = [tool, { ...tool, callId: "b", name: "write_file" }, { kind: "text" as const, id: "reply", text: "Your report is ready." }, { ...tool, callId: "error", isError: true, output: "Upload failed" }];
  const { rerender } = render(<WorkTrace conversation status="running" startedMs={0} durationMs={null} blocks={blocks} />);
  const activity = screen.getByRole("button", { name: "Reading files Creating files" });
  expect(activity.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(activity);
  fireEvent.click(activity);
  rerender(<WorkTrace conversation status="done" startedMs={0} durationMs={1000} blocks={blocks} />);
  expect(screen.queryByRole("button", { name: "Reading files Creating files" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Write file/ })).toBeNull();
  expect(screen.getByText("Your report is ready.")).toBeTruthy();
  expect(screen.getByText("Upload failed")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Thought for 1.0s" }));
  fireEvent.click(screen.getByRole("button", { name: "Read files Created files" }));
  fireEvent.click(screen.getByRole("button", { name: /Write file/ }));
  expect(screen.getByText("Report contents")).toBeTruthy();
});

it("does not offer a thought toggle when the turn is only a reply", () => {
  render(<WorkTrace conversation status="done" startedMs={0} durationMs={1000} blocks={[{ kind: "text", id: "a", text: "Hello." }]} />);
  expect(screen.queryByTestId("conversation-work-fold")).toBeNull();
  expect(screen.getByText("Hello.")).toBeTruthy();
});

it("hides intermediate replies behind one thought toggle once the turn completes", () => {
  const tool: ToolBlock = { kind: "tool", callId: "a", name: "read_file", input: { path: "report.csv" }, output: "Report contents", isError: false, durationMs: 100, approval: null, startedMs: 0 };
  const blocks = [
    tool,
    { kind: "text" as const, id: "think", text: "I will inspect the files first." },
    { ...tool, callId: "b", name: "list_dir" },
    { kind: "text" as const, id: "final", text: "The report is ready." },
  ];
  const { rerender } = render(<WorkTrace conversation status="running" startedMs={0} durationMs={null} blocks={blocks} />);
  expect(screen.getByText("I will inspect the files first.")).toBeTruthy();
  expect(screen.getByText("The report is ready.")).toBeTruthy();
  rerender(<WorkTrace conversation status="done" startedMs={0} durationMs={4000} blocks={blocks} />);
  expect(screen.queryByText("I will inspect the files first.")).toBeNull();
  expect(screen.queryByText("Used tools")).toBeNull();
  expect(screen.getByText("The report is ready.")).toBeTruthy();
  const toggle = screen.getByRole("button", { name: "Thought for 4.0s" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("I will inspect the files first.")).toBeTruthy();
  fireEvent.click(toggle);
  expect(screen.queryByText("I will inspect the files first.")).toBeNull();
});
