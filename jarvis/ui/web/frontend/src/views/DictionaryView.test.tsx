/**
 * Component tests for DictionaryView — the custom vocabulary tab of the voice
 * section.
 *
 * Pinned: corrections read as "heard → written" and plain terms as the term
 * alone; the search matches both sides; the editor is a document-level dialog
 * that validates before it sends anything, saves on Enter and sends the same
 * payload shapes the API has always taken; a delete can be undone from its
 * toast.
 *
 * Driven through a stubbed fetch. No jest-dom in this repo — assertions use
 * toBeTruthy()/toBeNull().
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { DictionaryView } from "./DictionaryView";

interface Entry {
  id: string;
  word: string;
  misheard: string[];
  created_at: string;
  updated_at: string;
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}

const CORRECTION: Entry = {
  id: "entry-1",
  word: "CallPost",
  misheard: ["call post"],
  created_at: "2026-08-09T00:00:00Z",
  updated_at: "2026-08-09T00:00:00Z",
};
const TERM: Entry = {
  id: "entry-2",
  word: "Claude",
  misheard: [],
  created_at: "2026-08-09T00:00:00Z",
  updated_at: "2026-08-09T00:00:00Z",
};

/** A tiny in-memory /api/dictionary that records every request. */
function installDictionary(initial: Entry[]) {
  let entries = [...initial];
  let next = 100;
  const calls: Call[] = [];
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(init.body as string) : null;
      calls.push({ url, method, body });
      if (method === "GET" && url === "/api/dictionary") return json({ entries });
      if (method === "POST" && url === "/api/dictionary") {
        const created = { id: `entry-${next++}`, created_at: "", updated_at: "", ...body };
        entries = [...entries, created];
        return json(created);
      }
      const id = decodeURIComponent(url.split("/").pop() ?? "");
      if (method === "PATCH") {
        const updated = { ...entries.find((e) => e.id === id)!, ...body };
        entries = entries.map((e) => (e.id === id ? updated : e));
        return json(updated);
      }
      if (method === "DELETE") {
        entries = entries.filter((e) => e.id !== id);
        return json({ ok: true });
      }
      throw new Error(`unexpected fetch ${method} ${url}`);
    }),
  );
  return calls;
}

/** jsdom has none; the Switch measures its hidden form input with one. */
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  setUiLanguage("en");
  useEventStore.setState({ toasts: [] });
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DictionaryView list", () => {
  it("shows a correction as heard → written and a term on its own", async () => {
    installDictionary([CORRECTION, TERM]);
    render(<DictionaryView hideHeader />);

    const rows = await screen.findAllByTestId("dictionary-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("call post");
    expect(rows[0].textContent).toContain("CallPost");
    expect(rows[0].textContent).toContain("Correction");
    expect(rows[1].textContent).toContain("Claude");
    expect(rows[1].textContent).toContain("Word");
    expect(screen.getByTestId("dictionary-count").textContent).toBe("2");
  });

  it("searches both the word and what is misheard", async () => {
    installDictionary([CORRECTION, TERM]);
    render(<DictionaryView hideHeader />);

    await screen.findAllByTestId("dictionary-row");
    fireEvent.change(screen.getByTestId("dictionary-search"), { target: { value: "CALL P" } });
    expect(screen.getAllByTestId("dictionary-row")).toHaveLength(1);

    fireEvent.change(screen.getByTestId("dictionary-search"), { target: { value: "zzz" } });
    expect(screen.queryByTestId("dictionary-list")).toBeNull();
    expect(screen.queryByTestId("dictionary-no-matches")).toBeTruthy();
  });

  it("shows a designed empty state with its own Add action", async () => {
    installDictionary([]);
    render(<DictionaryView hideHeader />);

    await waitFor(() => expect(screen.queryByTestId("dictionary-empty")).toBeTruthy());
    expect(screen.queryByTestId("dictionary-search")).toBeNull();
    fireEvent.click(screen.getByTestId("dictionary-empty-add"));
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("labels the row actions with the word they act on", async () => {
    installDictionary([TERM]);
    render(<DictionaryView hideHeader />);

    const edit = await screen.findByTestId("dictionary-edit-entry-2");
    expect(edit.getAttribute("aria-label")).toBe("Edit: Claude");
    expect(screen.getByTestId("dictionary-delete-entry-2").getAttribute("aria-label")).toBe(
      "Delete: Claude",
    );
  });

  it("keeps its own header when rendered standalone", async () => {
    installDictionary([]);
    const { container } = render(<DictionaryView />);

    await waitFor(() => expect(screen.queryByTestId("dictionary-empty")).toBeTruthy());
    expect(container.querySelector("header")).toBeTruthy();
    cleanup();

    const embedded = render(<DictionaryView hideHeader />);
    await waitFor(() => expect(screen.queryByTestId("dictionary-empty")).toBeTruthy());
    expect(embedded.container.querySelector("header")).toBeNull();
  });
});

describe("DictionaryView editor", () => {
  it("opens an existing correction immediately in a document-level dialog", async () => {
    installDictionary([CORRECTION]);
    const { container } = render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-edit-entry-1"));

    const dialog = await screen.findByRole("dialog");
    // Portalled out of the overflow-constrained hub pane.
    expect(container.contains(dialog)).toBe(false);
    expect((screen.getByTestId("dictionary-misheard-input") as HTMLInputElement).value).toBe(
      "call post",
    );
    expect((screen.getByTestId("dictionary-word-input") as HTMLInputElement).value).toBe(
      "CallPost",
    );
    // Focus lands in the first field, not on the close button.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByTestId("dictionary-misheard-input")),
    );
  });

  it("refuses an empty word with a message and sends nothing", async () => {
    const calls = installDictionary([]);
    render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-add"));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByTestId("dictionary-save"));

    expect(screen.getByTestId("dictionary-field-error").textContent).toBe(
      "Enter the word as it should be written.",
    );
    expect(screen.getByTestId("dictionary-word-input").getAttribute("aria-invalid")).toBe("true");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("asks for a misheard variant when correcting", async () => {
    const calls = installDictionary([]);
    render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-add"));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByTestId("dictionary-correction-toggle"));
    fireEvent.change(screen.getByTestId("dictionary-word-input"), {
      target: { value: "GitHub" },
    });
    fireEvent.click(screen.getByTestId("dictionary-save"));

    expect(screen.getByTestId("dictionary-field-error").textContent).toBe(
      "Enter at least one way it gets misheard.",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("adds a correction on Enter with the variants split at commas", async () => {
    const calls = installDictionary([]);
    render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-add"));
    await screen.findByRole("dialog");
    fireEvent.click(screen.getByTestId("dictionary-correction-toggle"));
    fireEvent.change(screen.getByTestId("dictionary-misheard-input"), {
      target: { value: "gitar, git hub ," },
    });
    fireEvent.change(screen.getByTestId("dictionary-word-input"), {
      target: { value: " GitHub " },
    });
    fireEvent.submit(screen.getByTestId("dictionary-word-input").closest("form")!);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ word: "GitHub", misheard: ["gitar", "git hub"] });
    expect((await screen.findAllByTestId("dictionary-row"))[0].textContent).toContain("GitHub");
  });

  it("saves an edit through PATCH", async () => {
    const calls = installDictionary([TERM]);
    render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-edit-entry-2"));
    await screen.findByRole("dialog");
    fireEvent.change(screen.getByTestId("dictionary-word-input"), {
      target: { value: "Claude Code" },
    });
    fireEvent.click(screen.getByTestId("dictionary-save"));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.url).toBe("/api/dictionary/entry-2");
    expect(patch?.body).toEqual({ word: "Claude Code", misheard: [] });
  });

  it("closes on Escape without saving", async () => {
    const calls = installDictionary([TERM]);
    render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-add"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("DictionaryView delete", () => {
  it("deletes at once and offers Undo, which adds the same entry back", async () => {
    const calls = installDictionary([CORRECTION]);
    render(<DictionaryView hideHeader />);

    fireEvent.click(await screen.findByTestId("dictionary-delete-entry-1"));
    await waitFor(() => expect(screen.queryByTestId("dictionary-row")).toBeNull());
    expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/dictionary/entry-1")).toBe(
      true,
    );

    const toast = useEventStore.getState().toasts.at(-1);
    expect(toast?.message).toBe('"CallPost" removed from the dictionary.');
    expect(toast?.action?.label).toBe("Undo");
    await toast!.action!.onAction();

    await waitFor(() => expect(screen.getAllByTestId("dictionary-row")).toHaveLength(1));
    const post = calls.find((c) => c.method === "POST");
    expect(post?.body).toEqual({ word: "CallPost", misheard: ["call post"] });
  });
});
