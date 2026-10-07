import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DragEvent as ReactDragEvent } from "react";
import { usePaneFileDrag } from "./paneFileDrag";
import { resetDragSessionForTests } from "./dragSessionEnd";
import { SKILL_DRAG_TYPE, dragCarriesSkill, loadSkillDrag, pasteSkillText, readSkillDrag } from "./skillDrag";

afterEach(() => resetDragSessionForTests());

/** A DataTransfer good enough for loading and reading a drag. */
function transfer(initial: Record<string, string> = {}): DataTransfer {
  const data = new Map(Object.entries(initial));
  return {
    get types() {
      return [...data.keys()];
    },
    effectAllowed: "all",
    dropEffect: "",
    files: [] as unknown as FileList,
    setData: (type: string, value: string) => void data.set(type, value),
    getData: (type: string) => data.get(type) ?? "",
  } as unknown as DataTransfer;
}

function dragEvent(dataTransfer: DataTransfer) {
  return { dataTransfer, preventDefault: vi.fn() } as unknown as ReactDragEvent;
}

const SKILL = { id: "s1", title: "Review", content: "# Review\n\nCheck it.", hue: "amber" };

describe("carrying a skill", () => {
  it("loads its own type and the text, and reads back what was loaded", () => {
    const dt = transfer();
    loadSkillDrag(dt, SKILL);

    expect(dt.types).toEqual([SKILL_DRAG_TYPE, "text/plain"]);
    expect(dt.getData("text/plain")).toBe(SKILL.content);
    expect(dragCarriesSkill(dt)).toBe(true);
    expect(readSkillDrag(dt)).toEqual(SKILL);
  });

  it("reads junk under the skill type as no skill", () => {
    expect(readSkillDrag(transfer({ [SKILL_DRAG_TYPE]: "{not json" }))).toBeNull();
    expect(readSkillDrag(transfer({ [SKILL_DRAG_TYPE]: JSON.stringify({ id: 1 }) }))).toBeNull();
  });
});

describe("a terminal pane taking a skill", () => {
  it("arms for a skill drag and says what it carries", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn(), vi.fn()));
    const dt = transfer();
    loadSkillDrag(dt, SKILL);

    act(() => result.current.handlers.onDragEnter(dragEvent(dt)));

    expect(result.current.dragging).toBe(true);
    expect(result.current.carrying).toBe("skill");
  });

  it("stays quiet for a skill drag when it takes no skills", () => {
    const { result } = renderHook(() => usePaneFileDrag(vi.fn()));
    const dt = transfer();
    loadSkillDrag(dt, SKILL);

    act(() => result.current.handlers.onDragEnter(dragEvent(dt)));

    expect(result.current.dragging).toBe(false);
  });

  it("hands the dropped skill over instead of treating it as files", () => {
    const onFiles = vi.fn();
    const onSkill = vi.fn();
    const { result } = renderHook(() => usePaneFileDrag(onFiles, onSkill));
    const dt = transfer();
    loadSkillDrag(dt, SKILL);

    act(() => {
      result.current.handlers.onDragEnter(dragEvent(dt));
      result.current.handlers.onDrop(dragEvent(dt));
    });

    expect(onSkill).toHaveBeenCalledWith(SKILL);
    expect(onFiles).not.toHaveBeenCalled();
    expect(result.current.dragging).toBe(false);
    expect(result.current.carrying).toBeNull();
  });

  it("still takes files when it also takes skills", () => {
    const onFiles = vi.fn();
    const { result } = renderHook(() => usePaneFileDrag(onFiles, vi.fn()));
    const dt = transfer({ Files: "" });

    act(() => {
      result.current.handlers.onDragEnter(dragEvent(dt));
    });
    expect(result.current.carrying).toBe("files");
    act(() => result.current.handlers.onDrop(dragEvent(dt)));

    expect(onFiles).toHaveBeenCalledTimes(1);
  });
});

describe("pasting a skill's text", () => {
  const terminal = (bracketedPasteMode: boolean) => ({ paste: vi.fn(), focus: vi.fn(), modes: { bracketedPasteMode } });

  it("pastes into a terminal in bracketed-paste mode, lines and all", () => {
    const term = terminal(true);
    expect(pasteSkillText(term, "one\ntwo")).toBe(true);
    expect(term.paste).toHaveBeenCalledWith("one\ntwo");
  });

  it("refuses a multi-line text where every line would run as a command", () => {
    const term = terminal(false);
    expect(pasteSkillText(term, "one\ntwo")).toBe(false);
    expect(term.paste).not.toHaveBeenCalled();
  });

  it("pastes a single line anywhere: paste never adds the Enter", () => {
    const term = terminal(false);
    expect(pasteSkillText(term, "one line")).toBe(true);
  });
});
