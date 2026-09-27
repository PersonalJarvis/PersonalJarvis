import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TaskOutputLinks } from "./TaskOutputLinks";

const actions = vi.hoisted(() => ({ local: vi.fn(async () => true), external: vi.fn(async () => true) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/lib/openLocalPath", () => ({ openLocalPath: actions.local }));
vi.mock("@/lib/openExternal", () => ({ openExternalUrl: actions.external }));

it("opens only checked files and explicit web links from a task handoff", () => {
  render(<TaskOutputLinks
    output={["chat:society:scout", "C:\\work\\report.md", "C:\\work\\missing.md", "https://example.com/source"]}
    evidence={["C:\\work\\report.md"]} />);
  fireEvent.click(screen.getByRole("button", { name: /report.md/ }));
  expect(actions.local).toHaveBeenCalledWith("C:\\work\\report.md");
  fireEvent.click(screen.getByRole("button", { name: /example.com/ }));
  expect(actions.external).toHaveBeenCalledWith("https://example.com/source");
  expect(screen.queryByText(/missing.md/)).toBeNull();
  expect(screen.queryByText(/chat:society/)).toBeNull();
});
