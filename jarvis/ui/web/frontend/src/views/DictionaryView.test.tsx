import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DictionaryView } from "./DictionaryView";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DictionaryView editor", () => {
  it("opens an existing correction immediately in a document-level dialog", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            entries: [
              {
                id: "entry-1",
                word: "CallPost",
                misheard: ["call post"],
                created_at: "2026-08-09T00:00:00Z",
                updated_at: "2026-08-09T00:00:00Z",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    render(<DictionaryView hideHeader />);
    const edit = await screen.findByTestId("dictionary-edit-entry-1");

    fireEvent.click(edit);

    const dialog = await screen.findByRole("dialog");
    expect(dialog.parentElement).toBe(document.body.lastElementChild);
    expect(
      (screen.getByTestId("dictionary-misheard-input") as HTMLInputElement).value,
    ).toBe("call post");
    expect(
      (screen.getByTestId("dictionary-word-input") as HTMLInputElement).value,
    ).toBe("CallPost");
    await waitFor(() => expect(dialog.isConnected).toBe(true));
  });
});
