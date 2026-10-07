import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IdeSkill } from "@/lib/ideSkillsApi";
import { useIdeSkillsStore } from "@/store/ideSkills";
import { PANE_PASTE_EVENT, SKILL_DRAG_TYPE, type PanePasteDetail } from "@/components/agentic/skillDrag";
import { SkillsTab } from "./SkillsTab";

/** An in-memory stand-in for `/api/agentic-ide/skills`. */
function fakeLibrary(initial: IdeSkill[] = []) {
  const skills = [...initial];
  let next = 1;
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const fetchFake = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    const id = url.split("/api/agentic-ide/skills/")[1]?.split("/")[0];
    if (method === "GET" && url.endsWith("/skills")) return json({ skills });
    if (method === "POST" && url.endsWith("/skills")) {
      const skill: IdeSkill = {
        id: `s${next++}`, title: body.title, content: body.content, description: body.description ?? "",
        hue: body.hue ?? "blue", icon: body.icon ?? "auto", created_at: "", updated_at: "", use_count: 0, last_used_at: null,
      };
      skills.unshift(skill);
      return json(skill, 201);
    }
    if (method === "POST" && url.endsWith("/used")) {
      const skill = skills.find((entry) => entry.id === id)!;
      skill.use_count += 1;
      return json(skill);
    }
    return json({ detail: "unexpected" }, 500);
  };
  return { skills, fetchFake };
}

const REVIEW: IdeSkill = {
  id: "r1", title: "Code review", content: "# Code review\n\nCheck the diff.", description: "Check the diff.",
  hue: "amber", icon: "shield", created_at: "", updated_at: "", use_count: 2, last_used_at: null,
};

beforeEach(() => {
  localStorage.clear();
  useIdeSkillsStore.setState({ skills: [], status: "idle", error: null, landing: null, dragging: null, target: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SkillsTab", () => {
  it("offers three ways in when the library is empty, and adds the examples", async () => {
    const library = fakeLibrary();
    vi.stubGlobal("fetch", library.fetchFake);
    render(<SkillsTab />);

    const examples = await screen.findByTestId("skills-empty-examples");
    fireEvent.click(examples);

    await waitFor(() => expect(library.skills).toHaveLength(3));
    // The first example of the list ends up on top.
    expect(await screen.findByRole("button", { name: "Plan before coding" })).toBeTruthy();
    expect(screen.queryByTestId("skills-empty")).toBeNull();
  });

  it("filters by title, summary and text", async () => {
    vi.stubGlobal("fetch", fakeLibrary([REVIEW, { ...REVIEW, id: "r2", title: "Release notes", content: "ship it", description: "" }]).fetchFake);
    render(<SkillsTab />);
    await screen.findByRole("button", { name: "Code review" });

    fireEvent.change(screen.getByTestId("skills-search"), { target: { value: "diff" } });

    expect(screen.getByRole("button", { name: "Code review" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Release notes" })).toBeNull();
  });

  it("pastes into the selected pane from the card's button", async () => {
    vi.stubGlobal("fetch", fakeLibrary([REVIEW]).fetchFake);
    useIdeSkillsStore.setState({ target: { workspaceId: "w1", pane: "mika" } });
    const received: PanePasteDetail[] = [];
    const listen = (event: Event) => received.push((event as CustomEvent<PanePasteDetail>).detail);
    window.addEventListener(PANE_PASTE_EVENT, listen);
    render(<SkillsTab />);

    fireEvent.click(await screen.findByTestId("skill-paste-r1"));
    window.removeEventListener(PANE_PASTE_EVENT, listen);

    expect(received).toEqual([{ workspaceId: "w1", pane: "mika", skill: { id: "r1", title: "Code review", content: REVIEW.content, hue: "amber" } }]);
  });

  it("loads the drag with the skill and announces it while it is carried", async () => {
    vi.stubGlobal("fetch", fakeLibrary([REVIEW]).fetchFake);
    render(<SkillsTab />);
    const card = await screen.findByTestId("skill-card-r1");
    const data = new Map<string, string>();
    const dataTransfer = {
      setData: (type: string, value: string) => data.set(type, value),
      setDragImage: vi.fn(),
      effectAllowed: "",
    };

    fireEvent.dragStart(card, { dataTransfer });

    expect(JSON.parse(data.get(SKILL_DRAG_TYPE) ?? "{}")).toMatchObject({ id: "r1", content: REVIEW.content });
    expect(data.get("text/plain")).toBe(REVIEW.content);
    expect(useIdeSkillsStore.getState().dragging).toMatchObject({ id: "r1", title: "Code review" });

    fireEvent.dragEnd(card);
    expect(useIdeSkillsStore.getState().dragging).toBeNull();
  });

  it("saves a pasted text as a new skill from the editor", async () => {
    const library = fakeLibrary([REVIEW]);
    vi.stubGlobal("fetch", library.fetchFake);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByTestId("skills-new"));

    fireEvent.change(screen.getByTestId("skill-editor-content"), { target: { value: "# House style\n\nShort sentences." } });
    // No title typed: the heading is suggested and used.
    expect((screen.getByTestId("skill-editor-title") as HTMLInputElement).placeholder).toBe("House style");
    await act(async () => {
      fireEvent.click(screen.getByTestId("skill-editor-save"));
    });

    await waitFor(() => expect(library.skills[0]?.title).toBe("House style"));
    expect(screen.queryByTestId("skill-editor")).toBeNull();
    expect(screen.getByRole("button", { name: "House style" })).toBeTruthy();
  });

  it("shows where a skill just landed and counts the paste", async () => {
    vi.stubGlobal("fetch", fakeLibrary([REVIEW]).fetchFake);
    render(<SkillsTab />);
    await screen.findByTestId("skill-card-r1");

    act(() => useIdeSkillsStore.getState().recordUse("r1", "mika"));

    expect(await screen.findByTestId("skill-landed-r1")).toBeTruthy();
    expect(useIdeSkillsStore.getState().skills[0].use_count).toBe(3);
  });
});
