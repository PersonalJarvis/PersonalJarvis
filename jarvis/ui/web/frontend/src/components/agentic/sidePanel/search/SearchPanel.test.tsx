import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useCodeEditorStore } from "@/store/codeEditor";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { SearchPanel } from "./SearchPanel";

const RESULT = {
  results: [
    {
      path: "src/app.py",
      matches: [{ line: 2, column: 12, length: 5, preview: "    return 'Hello'", preview_start: 12 }],
    },
  ],
  match_count: 1,
  file_count: 1,
  searched_files: 3,
  truncated: false,
};

const calls: { url: string; body: string }[] = [];

beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: String(init?.body ?? "") });
      const body = url.includes("/search/replace")
        ? { replaced_files: ["src/app.py"], replacements: 1, skipped: [] }
        : RESULT;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  useEventStore.setState({ activeSection: "agentic-ide" });
  useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" }, stagedPane: null });
  useCodeEditorStore.setState({ tabs: [], active: {}, files: {}, visible: false, reveal: null, closed: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SearchPanel", () => {
  it("searches with the chosen options and opens a match selected in the editor", async () => {
    render(<SearchPanel />);
    fireEvent.change(screen.getByTestId("ide-search-input"), { target: { value: "hello" } });
    fireEvent.click(screen.getByLabelText("Match case"));

    const match = await screen.findByTestId("ide-search-match");
    const url = calls.find((call) => call.url.includes("/search?"))!.url;
    expect(url).toContain("q=hello");
    expect(url).toContain("case=true");
    expect(match.querySelector("mark")?.textContent).toBe("Hello");

    fireEvent.click(match);
    expect(useCodeEditorStore.getState().tabs).toMatchObject([{ path: "src/app.py" }]);
    expect(useCodeEditorStore.getState().reveal).toMatchObject({ line: 2, column: 12, length: 5 });
  });

  it("replaces only after a confirming second click, and leaves unsaved files out", async () => {
    useCodeEditorStore.getState().openFile("w1", "src/other.py");
    render(<SearchPanel />);
    fireEvent.change(screen.getByTestId("ide-search-input"), { target: { value: "hello" } });
    await screen.findByTestId("ide-search-match");
    fireEvent.click(screen.getByLabelText("Show or hide replace"));
    fireEvent.change(screen.getByTestId("ide-search-replace"), { target: { value: "Hi" } });

    fireEvent.click(screen.getByTestId("ide-search-replace-all"));
    expect(calls.some((call) => call.url.includes("/search/replace"))).toBe(false);
    fireEvent.click(screen.getByTestId("ide-search-replace-all"));

    await waitFor(() => expect(calls.some((call) => call.url.includes("/search/replace"))).toBe(true));
    const sent = JSON.parse(calls.find((call) => call.url.includes("/search/replace"))!.body);
    expect(sent).toMatchObject({ query: "hello", replacement: "Hi", paths: ["src/app.py"] });
  });
});
