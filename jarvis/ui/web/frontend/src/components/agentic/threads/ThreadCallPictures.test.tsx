import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { TurnBlock, TurnItem } from "@/components/agentchat/reduce";
import { callPictures, ThreadTimeline } from "./ThreadTimeline";

const viewed: TurnBlock = {
  kind: "tool", callId: "c1", name: "Read", input: { file_path: "/repo/shot.png" }, output: "[image]", isError: false,
  durationMs: 100, approval: null, startedMs: 1,
};
const picture: TurnBlock = { kind: "text", id: "media-abc", text: "![image](</api/outputs/s/files/shot.png/download?disposition=inline>)" };
const answer: TurnBlock = { kind: "text", id: "m1", text: "The menu shows three entries." };

function turn(blocks: TurnBlock[]): TurnItem {
  return {
    type: "turn", id: "t1", provider: "claude-api", model: "", effort: "", runner: "", status: "done", blocks,
    startedMs: 1, durationMs: 5000, usage: null, liveUsage: null, costUsd: null, error: null,
  };
}

afterEach(cleanup);

describe("pictures a tool brought back", () => {
  it("belong to the call that brought them, not to the answer", () => {
    const { byCall, moved } = callPictures([viewed, picture, answer]);
    expect(byCall.get("c1")).toEqual([picture.kind === "text" ? picture.text : ""]);
    expect([...moved]).toEqual(["media-abc"]);
  });

  it("leave a picture after the agent's own words where it is", () => {
    expect(callPictures([viewed, answer, { ...picture, id: "media-def" }]).moved.size).toBe(0);
  });

  it("show only once the call's line is opened", () => {
    render(<ThreadTimeline items={[turn([viewed, picture, answer])]} sessionId="s1" bottomInset={0} />);
    expect(screen.getByText("The menu shows three entries.")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    fireEvent.click(screen.getByTestId("thread-worked-for"));
    expect(document.querySelector("img")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Viewed an image/i }));
    expect(document.querySelector("img")?.getAttribute("src")).toContain("/api/outputs/s/files/shot.png");
  });
});
