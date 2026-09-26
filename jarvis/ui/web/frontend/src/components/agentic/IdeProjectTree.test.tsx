import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { IdeProjectTree } from "./IdeProjectTree";
import { useIdeProjectsStore } from "@/store/ideProjects";
import type { IdeProject } from "@/lib/agenticIdeApi";

afterEach(cleanup);

it("preserves a manually collapsed active project across graph refreshes", () => {
  const project = { id: "p1", path: "/app", name: "App", archived: false, scratch: false,
    workspaces: [{ id: "w1", name: "Work", status: "open", live_terminals: 1, terminals: 1, restorable: true }] } as IdeProject;
  useIdeProjectsStore.setState({ projects: [project], activeWorkspaceId: "w1", action: null });
  render(<IdeProjectTree />);
  const selected = screen.getByTestId("ide-workspace-w1");
  expect(selected.className).toContain("bg-muted");
  expect(selected.className).not.toContain("bg-accent");
  const toggle = screen.getByRole("button", { name: "Collapse App" });
  fireEvent.click(toggle);
  expect(screen.queryByTestId("ide-workspace-w1")).toBeNull();
  act(() => useIdeProjectsStore.getState().publish([{ ...project, workspaces: [...project.workspaces] }], "w1"));
  expect(screen.queryByTestId("ide-workspace-w1")).toBeNull();
});
