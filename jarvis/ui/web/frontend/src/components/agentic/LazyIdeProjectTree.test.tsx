import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LazyIdeProjectTree } from "./LazyIdeProjectTree";
import { useEventStore } from "@/store/events";

const load = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { modules: 0, broken: false, ready, release };
});
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, translate: (key: string) => key }));
vi.mock("./IdeProjectTree", async () => {
  load.modules += 1;
  await load.ready;
  return { IdeProjectTree: () => {
    if (load.broken) throw new Error("Project tree render failed");
    return <input aria-label="Project filter" />;
  } };
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("defers project code, keeps its instance through updates and contains a failed tree", async () => {
  expect(load.modules).toBe(0);
  const view = render(<LazyIdeProjectTree />);
  expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  await act(async () => load.release());
  const filter = await screen.findByLabelText("Project filter");
  expect(load.modules).toBe(1);
  fireEvent.change(filter, { target: { value: "Current project" } });
  view.rerender(<LazyIdeProjectTree />);
  expect(screen.getByLabelText("Project filter")).toBe(filter);
  expect((filter as HTMLInputElement).value).toBe("Current project");

  vi.spyOn(console, "error").mockImplementation(() => undefined);
  useEventStore.setState({ activeSection: "agentic-ide" });
  load.broken = true;
  view.rerender(<LazyIdeProjectTree />);
  fireEvent.click(await screen.findByRole("button", { name: "view_error_boundary.back_to_chats" }));
  await waitFor(() => expect(useEventStore.getState().activeSection).toBe("chats"));
});
