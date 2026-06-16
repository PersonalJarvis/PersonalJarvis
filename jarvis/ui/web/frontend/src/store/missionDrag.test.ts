import { describe, it, expect, beforeEach } from "vitest";
import { useMissionDrag } from "./missionDrag";

describe("useMissionDrag", () => {
  beforeEach(() => {
    useMissionDrag.getState().end();
  });

  it("starts not dragging", () => {
    expect(useMissionDrag.getState().dragging).toBe(false);
  });

  it("begin() marks a mission drag in progress", () => {
    useMissionDrag.getState().begin();
    expect(useMissionDrag.getState().dragging).toBe(true);
  });

  it("end() clears the drag", () => {
    useMissionDrag.getState().begin();
    useMissionDrag.getState().end();
    expect(useMissionDrag.getState().dragging).toBe(false);
  });
});
