/**
 * The pane header's recap line and the card behind it.
 *
 * What is pinned here is what the version this replaces got wrong: the long
 * form has to be reachable and readable, a thin recap has to say WHY it is
 * thin, and the user has to be able to overrule it. The old tooltip failed all
 * three — it could not be moved into, it never explained itself, and there was
 * nothing to press.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PaneRecap } from "./PaneRecap";

const BASE = {
  name: "Mika",
  displayName: "Claude Code",
  light: false,
};

describe("the header line", () => {
  it("shows the recap in place of the agent name", () => {
    render(<PaneRecap {...BASE} recap="Rewrote the auth middleware" />);

    expect(screen.getByTestId("pane-recap-Mika").textContent).toBe(
      "Rewrote the auth middleware",
    );
    expect(screen.queryByTestId("pane-agent-Mika")).toBeNull();
  });

  it("keeps showing which CLI runs here until there is a recap", () => {
    render(<PaneRecap {...BASE} />);

    expect(screen.getByTestId("pane-agent-Mika").textContent).toBe("Claude Code");
    expect(screen.queryByTestId("pane-recap-Mika")).toBeNull();
  });
});

describe("the recap card", () => {
  it("opens on click and carries the full long form", () => {
    render(
      <PaneRecap
        {...BASE}
        recap="Rewrote the auth middleware"
        detail="The goal was to fix the failing login test. The session lookup in auth/middleware.py now reads the token once. Two tests still fail."
      />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));

    const card = screen.getByTestId("pane-recap-card-Mika");
    expect(card.getAttribute("role")).toBe("dialog");
    // The whole point of opening it: the sentence the header clipped, entire.
    expect(screen.getByTestId("pane-recap-detail-Mika").textContent).toContain(
      "Two tests still fail.",
    );
    expect(card.textContent).toContain("Claude Code");
  });

  it("does not cover the terminal when the pointer only passes over the line", () => {
    render(<PaneRecap {...BASE} recap="Rewrote the auth middleware" />);

    const line = screen.getByTestId("pane-recap-Mika");
    fireEvent.mouseEnter(line.parentElement!);
    expect(screen.queryByTestId("pane-recap-card-Mika")).toBeNull();
  });

  it("closes on Escape", () => {
    render(<PaneRecap {...BASE} recap="Rewrote the auth middleware" />);

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));
    fireEvent.keyDown(document, { key: "Escape" });

    expect(screen.queryByTestId("pane-recap-card-Mika")).toBeNull();
  });

  it("repeats nothing when the long form says what the line says", () => {
    render(
      <PaneRecap {...BASE} recap="Not started yet." detail="Not started yet." />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));

    expect(screen.queryByTestId("pane-recap-detail-Mika")).toBeNull();
  });
});

describe("why the recap is what it is", () => {
  it("says so when nothing could summarize the pane, and what went wrong", () => {
    render(
      <PaneRecap
        {...BASE}
        recap="Running pytest"
        detail="Working now: Running pytest."
        source="heuristic"
        reason="unavailable"
        note="AuthenticationError: invalid x-api-key"
      />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));

    const why = screen.getByTestId("pane-recap-why-Mika");
    expect(why.textContent).toContain("No model could summarize this pane");
    // The provider's own words, so a broken key is a fixable fact rather than
    // an unexplained thin sentence.
    expect(why.textContent).toContain("AuthenticationError");
  });

  it("credits the model that wrote it instead of explaining itself", () => {
    render(
      <PaneRecap
        {...BASE}
        recap="Rewrote the auth middleware"
        detail="Where the work stands."
        source="model"
        reason="summarized"
        writer="claude-opus-5"
        generatedAt={Date.now() / 1000 - 30}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));

    const card = screen.getByTestId("pane-recap-card-Mika");
    expect(card.textContent).toContain("claude-opus-5");
    expect(card.textContent).toContain("30s ago");
    // A summary needs no excuse — the footer already says who and when.
    expect(screen.queryByTestId("pane-recap-why-Mika")).toBeNull();
  });

  it("explains a pane that has printed too little to summarize", () => {
    render(
      <PaneRecap {...BASE} recap="Running — nothing printed yet." reason="warming" />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));

    expect(screen.getByTestId("pane-recap-why-Mika").textContent).toContain(
      "Too little output so far",
    );
  });
});

describe("writing the recap yourself", () => {
  it("opens the editor from the clearly labelled action in the card", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PaneRecap {...BASE} recap="Running pytest" onSave={onSave} />);

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));
    const edit = screen.getByTestId("pane-recap-card-edit-Mika");
    expect(edit.textContent).toContain("Write it yourself");
    fireEvent.click(edit);

    const line = screen.getByTestId("pane-recap-input-Mika") as HTMLInputElement;
    // Pre-filled with what is on screen: correcting a recap is the common case,
    // replacing it from nothing the rare one.
    expect(line.value).toBe("Running pytest");

    fireEvent.change(line, { target: { value: "Demo branch — leave alone" } });
    fireEvent.change(screen.getByTestId("pane-recap-detail-input-Mika"), {
      target: { value: "Recording for the Thursday walkthrough." },
    });
    fireEvent.click(screen.getByTestId("pane-recap-save-Mika"));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        "Demo branch — leave alone",
        "Recording for the Thursday walkthrough.",
      ),
    );
  });

  it("saves on Ctrl+Enter without reaching for the mouse", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PaneRecap {...BASE} recap="Running pytest" onSave={onSave} />);

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));
    fireEvent.click(screen.getByTestId("pane-recap-card-edit-Mika"));
    fireEvent.keyDown(screen.getByTestId("pane-recap-input-Mika"), {
      key: "Enter",
      ctrlKey: true,
    });

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("Running pytest", ""));
  });

  it("shows what went wrong rather than closing on a failed save", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("That workspace is not open."));
    render(<PaneRecap {...BASE} recap="Running pytest" onSave={onSave} />);

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));
    fireEvent.click(screen.getByTestId("pane-recap-card-edit-Mika"));
    fireEvent.click(screen.getByTestId("pane-recap-save-Mika"));

    await waitFor(() =>
      expect(screen.getByTestId("pane-recap-error-Mika").textContent).toContain(
        "That workspace is not open.",
      ),
    );
    // The text is still in the editor: a failed save must not eat what was typed.
    expect(screen.getByTestId("pane-recap-input-Mika")).toBeTruthy();
  });

  it("keeps editing out of both the header and a read-only card", () => {
    render(<PaneRecap {...BASE} recap="Running pytest" />);

    expect(screen.queryByTestId("pane-recap-edit-Mika")).toBeNull();
    fireEvent.click(screen.getByTestId("pane-recap-Mika"));
    expect(screen.queryByTestId("pane-recap-card-edit-Mika")).toBeNull();
  });
});

describe("the card's actions", () => {
  it("offers a refresh on an automatic recap", async () => {
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    render(
      <PaneRecap {...BASE} recap="Running pytest" source="model" onRefresh={onRefresh} />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));
    const refresh = screen.getByTestId("pane-recap-refresh-Mika");
    expect(refresh.textContent).toContain("Summarize this pane again");
    fireEvent.click(refresh);

    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    // The card stays open — you asked for a new sentence, you want to read it.
    expect(screen.getByTestId("pane-recap-card-Mika")).toBeTruthy();
  });

  it("offers a reset instead once the user has written one", async () => {
    const onClear = vi.fn().mockResolvedValue(undefined);
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    render(
      <PaneRecap
        {...BASE}
        recap="Demo branch — leave alone"
        source="user"
        reason="pinned"
        onClear={onClear}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByTestId("pane-recap-Mika"));

    expect(screen.queryByTestId("pane-recap-refresh-Mika")).toBeNull();
    fireEvent.click(screen.getByTestId("pane-recap-reset-Mika"));

    await waitFor(() => expect(onClear).toHaveBeenCalled());
    expect(onRefresh).not.toHaveBeenCalled();
  });
});
