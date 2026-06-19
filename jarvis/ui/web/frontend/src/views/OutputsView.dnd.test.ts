import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  startMissionDrag,
  MISSION_DND_MIME,
  type OutputsDragMeta,
} from "./OutputsView";
import { useMissionDrag } from "@/store/missionDrag";

function fakeDragEvent() {
  const setData = vi.fn();
  const setDragImage = vi.fn();
  const dataTransfer = {
    setData,
    setDragImage,
    effectAllowed: "",
  } as unknown as DataTransfer;
  return { dataTransfer, setData, setDragImage };
}

const meta: OutputsDragMeta = {
  slug: "mission_019ecc78",
  utterance: "Build the landing page",
  status: "success",
};

describe("startMissionDrag", () => {
  beforeEach(() => useMissionDrag.getState().end());

  it("writes the mission payload under the shared MIME type", () => {
    const e = fakeDragEvent();
    startMissionDrag(e as unknown as React.DragEvent, meta);
    expect(e.setData).toHaveBeenCalledTimes(1);
    const [mime, json] = e.setData.mock.calls[0];
    expect(mime).toBe(MISSION_DND_MIME);
    expect(JSON.parse(json).slug).toBe("mission_019ecc78");
    expect(JSON.parse(json).utterance).toBe("Build the landing page");
  });

  it("installs the compact custom drag image (no giant native ghost)", () => {
    const e = fakeDragEvent();
    startMissionDrag(e as unknown as React.DragEvent, meta);
    expect(e.setDragImage).toHaveBeenCalledTimes(1);
  });

  it("marks a mission drag in progress so the dock can bloom", () => {
    const e = fakeDragEvent();
    expect(useMissionDrag.getState().dragging).toBe(false);
    startMissionDrag(e as unknown as React.DragEvent, meta);
    expect(useMissionDrag.getState().dragging).toBe(true);
  });
});
