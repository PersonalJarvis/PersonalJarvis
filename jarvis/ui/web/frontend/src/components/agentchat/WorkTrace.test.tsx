import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { groupTrace, groupActivityTrace, WorkTrace, traceDuration } from "./WorkTrace";
import { traceToolIdentity } from "./traceActivity";
import type { ToolBlock, TurnBlock, TurnStatus } from "./reduce";

const tool = (id: string, over: Partial<ToolBlock> = {}): ToolBlock => ({
  kind: "tool", callId: id, name: "read_file", input: { path: `${id}.ts` }, output: `Contents of ${id}`,
  isError: false, durationMs: 800, approval: null, startedMs: 1000, ...over,
});
const thought: TurnBlock = { kind: "reasoning", id: "reason", text: "**Check** the input.", live: false, durationMs: 8000, startedMs: 1000 };
const props = { startedMs: 1000, durationMs: 12000, status: "done" as TurnStatus };
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("work trace", () => {
  it("summarizes integrations and mutations in first-use order with original logos", () => {
    const blocks = [tool("linear", {name:"mcp__codex_apps__linear_list_issues"}), tool("edit", {name:"apply_patch"}), tool("shell", {name:"exec_command"}), tool("again", {name:"linear/get_issue"})];
    const {container} = render(<WorkTrace {...props} blocks={blocks} />);
    const summary = screen.getByRole("button", {name:"Used Linear Edited files Ran commands"});
    expect(summary.getAttribute("aria-expanded")).toBe("false");
    expect(summary.querySelector("img, [data-logo]")).toBeTruthy();
    expect(container.querySelector("[data-trace-tool]")).toBeNull();
    fireEvent.click(summary);
    expect(container.querySelectorAll("[data-trace-tool]")).toHaveLength(4);
    fireEvent.click(screen.getByRole("button",{name:/Linear · list issues/}));
    expect(screen.getByText("mcp__codex_apps__linear_list_issues")).toBeTruthy();
    expect(screen.getByText("Contents of linear")).toBeTruthy();
  });

  it("never folds failures, approvals, unfinished tools or replies into a successful summary", () => {
    const barriers: TurnBlock[] = [tool("fail",{isError:true}), tool("pending",{output:null}), tool("approval",{approval:{approvalId:"ap",summary:"Confirm",decision:null}}), {kind:"text",id:"reply",text:"Update"}];
    for (const barrier of barriers) {
      const groups = groupActivityTrace([tool("a",{name:"linear/get_issue"}),tool("b",{name:"exec_command"}),barrier,tool("c")]);
      expect(groups).toHaveLength(3);
      expect(groups[1].blocks).toEqual([barrier]);
    }
  });

  it("keeps live mixed activity open, then folds it at completion", () => {
    const blocks = [tool("a",{name:"linear/get_issue"}),tool("b",{name:"exec_command"})];
    const {rerender} = render(<WorkTrace {...props} status="running" blocks={blocks} />);
    expect(screen.getByRole("button",{name:"Using Linear Running commands"}).getAttribute("aria-expanded")).toBe("true");
    rerender(<WorkTrace {...props} blocks={blocks} />);
    expect(screen.getByRole("button",{name:"Used Linear Ran commands"}).getAttribute("aria-expanded")).toBe("false");
  });

  it("does not infer a plugin from arbitrary input text or substring names", () => {
    expect(traceToolIdentity(tool("shell",{name:"run_shell",input:{command:"echo linear"}})).integration).toBe(false);
    expect(traceToolIdentity(tool("math",{name:"nonlinear_solver"})).identity.logo).toBeUndefined();
    const unknown = traceToolIdentity(tool("other",{name:"mcp__custom_server__lookup"}));
    expect(unknown.integration).toBe(true);
    expect(unknown.service).toBe("Custom Server");
    expect(unknown.identity.logo).toBeUndefined();
  });

  it("preserves reasoning, call, next step and final answer order", () => {
    const blocks = [thought, tool("a"), { kind: "text" as const, id: "next", text: "Next, check the tests." }, tool("b"), { kind: "text" as const, id: "final", text: "Everything is ready." }];
    render(<WorkTrace {...props} blocks={blocks} />);
    const text = screen.getByTestId("work-trace").textContent!;
    expect(text.indexOf("Check the input")).toBeLessThan(text.indexOf("a.ts"));
    expect(text.indexOf("a.ts")).toBeLessThan(text.indexOf("Next, check"));
    expect(text.indexOf("Next, check")).toBeLessThan(text.indexOf("b.ts"));
    expect(text.indexOf("b.ts")).toBeLessThan(text.indexOf("Everything is ready"));
    expect(text.indexOf("Everything is ready")).toBeLessThan(text.indexOf("Done"));
  });

  it("groups fourteen successful reads and expands their full receipts", () => {
    const blocks = Array.from({length: 14}, (_, i) => tool(`file${i}`));
    render(<WorkTrace {...props} blocks={blocks} />);
    const group = screen.getByRole("button", { name: "Read 14 files" });
    expect(group.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("file0.ts")).toBeNull();
    fireEvent.click(group);
    fireEvent.click(screen.getByRole("button", { name: /Read file file0.ts/ }));
    expect(screen.getByText('"path": "file0.ts"', { exact: false })).toBeTruthy();
    expect(screen.getByText("Contents of file0")).toBeTruthy();
    expect(screen.getByText("read_file")).toBeTruthy();
  });

  it("never groups across reasoning, errors, approvals, active calls or mutations", () => {
    const blocks: TurnBlock[] = [tool("a"), thought, tool("b"), tool("err", {isError:true}), tool("c"), tool("approve", {approval:{approvalId:"ap",summary:"Confirm",decision:null}}), tool("d"), tool("running", {output:null}), tool("e"), tool("write1", {name:"write_file"}), tool("write2", {name:"write_file"})];
    expect(groupTrace(blocks)).toHaveLength(blocks.length);
  });

  it("automatically collapses completed groups even after live interaction", () => {
    const blocks = [tool("a"), tool("b")];
    const { rerender } = render(<WorkTrace {...props} status="running" blocks={blocks} />);
    const group = screen.getByRole("button", { name: "Read 2 files" });
    fireEvent.click(group); fireEvent.click(group);
    expect(group.getAttribute("aria-expanded")).toBe("true");
    rerender(<WorkTrace {...props} blocks={blocks} />);
    expect(group.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps errors and pending approval visible beside folded success", () => {
    render(<WorkTrace {...props} blocks={[tool("a"),tool("b"),tool("err",{isError:true,output:"Permission denied"}),tool("approval",{output:null,approval:{approvalId:"ap",summary:"Delete generated files?",decision:null}})]} onDecide={() => undefined} />);
    expect(screen.getByText("Permission denied")).toBeTruthy();
    expect(screen.getByText("Delete generated files?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" })).toBeTruthy();
  });

  it.each(["allow", "allow_always", "deny"] as const)("submits %s exactly once and reports failures", async decision => {
    const calls: string[] = [];
    let reject!: (error: Error) => void;
    const onDecide = (id: string, value: string) => { calls.push(`${id}:${value}`); return new Promise<void>((_, fail) => { reject = fail; }); };
    render(<WorkTrace {...props} status="running" blocks={[tool("ap",{output:null,approval:{approvalId:"approval-id",summary:"Confirm this action",decision:null}})]} onDecide={onDecide} />);
    const name = {allow:"Approve",allow_always:"Always allow",deny:"Deny"}[decision];
    const button = screen.getByRole("button", {name});
    fireEvent.click(button); fireEvent.click(button);
    expect(calls).toEqual([`approval-id:${decision}`]);
    await act(async () => reject(new Error("Could not save decision")));
    expect(screen.getByRole("alert").textContent).toBe("Could not save decision");
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  });

  it("shows a denied action without a running indicator", () => {
    const {container} = render(<WorkTrace {...props} status="running" blocks={[tool("a",{output:null,approval:{approvalId:"ap",summary:"Delete?",decision:"deny"}})]} />);
    expect(container.querySelector('[data-state="denied"]')).toBeTruthy();
    expect(screen.queryByRole("button", {name:"Approve"})).toBeNull();
  });

  it("renders edit input, a diff and output on demand", () => {
    render(<WorkTrace {...props} blocks={[tool("edit",{name:"Edit",input:{file_path:"app.ts",old_string:"oldValue",new_string:"newValue"},output:"File updated"})]} />);
    fireEvent.click(screen.getByRole("button",{name:/Edit/}));
    expect(screen.getByLabelText("Changes").textContent).toContain("− oldValue");
    expect(screen.getByLabelText("Changes").textContent).toContain("+ newValue");
    expect(screen.getByText("File updated")).toBeTruthy();
  });

  it.each(["cancelled", "error"] as const)("does not leave tools spinning after %s", status => {
    const {container} = render(<WorkTrace {...props} status={status} blocks={[tool("a",{output:null})]} />);
    expect(container.querySelector('[data-state="interrupted"]')).toBeTruthy();
    expect(container.querySelector('[data-state="running"]')).toBeNull();
  });

  it("updates the live clock and releases timers at completion", () => {
    vi.useFakeTimers(); vi.setSystemTime(1000);
    const {rerender} = render(<WorkTrace {...props} durationMs={null} status="running" blocks={[]} />);
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByRole("status").textContent).toContain("3.0s");
    rerender(<WorkTrace {...props} blocks={[]} />);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not interpret tool output as HTML", () => {
    const {container} = render(<WorkTrace {...props} blocks={[tool("a",{output:'<img src=x onerror=alert(1)>'})]} />);
    fireEvent.click(screen.getByRole("button", {name:/Read file/}));
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeTruthy();
  });

  it("formats durations without a 60-second remainder", () => {
    expect(traceDuration(119999)).toBe("1m 59s");
    expect(traceDuration(-1)).toBe("0.0s");
  });

  it.each([1, 49, 99])("preserves a measured %i ms call instead of displaying zero", ms => {
    expect(traceDuration(ms)).toBe(`${ms}ms`);
  });

  it.each(["RunCommand", "RunShellCommand", "run_shell"])("shows the short duration beside a readable %s label", name => {
    render(<WorkTrace {...props} blocks={[tool("shell", { name, input: { command: "read skill instructions" }, durationMs: 49 })]} />);
    expect(screen.getByRole("button", { name: /Run command.*49ms/ })).toBeTruthy();
    expect(screen.queryByText("0.0s")).toBeNull();
  });
});
