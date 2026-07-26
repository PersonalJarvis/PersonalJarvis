import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResumeCard, resumeSummary, savedAgo } from "./ResumeCard";
import type { ResumeOffer } from "@/lib/agenticIdeApi";

const OFFER: ResumeOffer = {
  available: true,
  folder: "/home/ruben/code/project",
  folder_name: "project",
  folder_exists: true,
  saved_at: 1_753_473_600,
  session_id: "ide_1",
  resumable_count: 1,
  terminals: [
    {
      key: "mika",
      name: "Mika",
      agent: "claude",
      display_name: "Claude Code",
      column: 0,
      slot: 0,
      available: true,
      resumable: true,
      prompts_sent: 3,
    },
    {
      key: "nova",
      name: "Nova",
      agent: "codex",
      display_name: "Codex",
      column: 1,
      slot: 0,
      available: true,
      resumable: false,
      prompts_sent: 0,
    },
  ],
};

afterEach(cleanup);

/** The project has no jest-dom matchers, so buttons are inspected directly. */
function button(testId: string): HTMLButtonElement {
  return screen.getByTestId(testId) as HTMLButtonElement;
}

describe("ResumeCard", () => {
  it("names the workspace and every pane that comes back", () => {
    render(
      <ResumeCard offer={OFFER} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByText("project")).toBeTruthy();
    expect(screen.getByText("Mika")).toBeTruthy();
    expect(screen.getByText("Nova")).toBeTruthy();
    expect(screen.getByText("Claude Code")).toBeTruthy();
  });

  it("says which conversations will NOT come back, before the click", () => {
    // The honesty requirement: an empty pane and a continued one look identical
    // until the agent is asked a follow-up question, so the difference has to be
    // visible while the choice is still being made.
    render(
      <ResumeCard offer={OFFER} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByTestId("resume-pane-nova").textContent).toMatch(/starts fresh/i);
    expect(screen.getByTestId("resume-pane-mika").textContent).not.toMatch(
      /starts fresh/i,
    );
  });

  it("marks a pane whose coding CLI is gone from this machine", () => {
    const offer: ResumeOffer = {
      ...OFFER,
      terminals: [
        OFFER.terminals[0],
        { ...OFFER.terminals[1], available: false },
      ],
    };
    render(
      <ResumeCard offer={offer} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByTestId("resume-pane-nova").textContent).toMatch(
      /not installed here/i,
    );
  });

  it("resumes and dismisses through its two buttons", () => {
    const onResume = vi.fn();
    const onDismiss = vi.fn();
    render(
      <ResumeCard
        offer={OFFER}
        busy={false}
        onResume={onResume}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.click(screen.getByTestId("resume-all"));
    expect(onResume).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("resume-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("cannot be resumed once its folder is gone", () => {
    const gone: ResumeOffer = { ...OFFER, available: false, folder_exists: false };
    render(
      <ResumeCard offer={gone} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(button("resume-all").disabled).toBe(true);
    expect(screen.getByText(/moved or deleted/i)).toBeTruthy();
    // ...but "start fresh" still works, or the card could never be cleared away.
    expect(button("resume-dismiss").disabled).toBe(false);
  });

  it("locks both buttons while a resume is in flight", () => {
    render(
      <ResumeCard offer={OFFER} busy onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(button("resume-all").disabled).toBe(true);
    expect(button("resume-dismiss").disabled).toBe(true);
  });
});

describe("resumeSummary", () => {
  it("distinguishes all, some and none", () => {
    expect(resumeSummary({ ...OFFER, resumable_count: 2 })).toMatch(
      /each continuing/i,
    );
    expect(resumeSummary(OFFER)).toMatch(/1 continue/i);
    expect(resumeSummary({ ...OFFER, resumable_count: 0 })).toMatch(
      /none of their conversations/i,
    );
  });

  it("leads with the reason nothing can be reopened", () => {
    expect(resumeSummary({ ...OFFER, folder_exists: false })).toMatch(
      /no longer on this machine/i,
    );
    expect(resumeSummary({ ...OFFER, available: false })).toMatch(
      /not installed/i,
    );
  });
});

describe("savedAgo", () => {
  it("stays coarse enough to be useful", () => {
    const now = 1_753_473_600_000;
    expect(savedAgo(1_753_473_600, now)).toBe("just now");
    expect(savedAgo(1_753_473_600 - 60 * 5, now)).toBe("5 minutes ago");
    expect(savedAgo(1_753_473_600 - 3600, now)).toBe("1 hour ago");
    expect(savedAgo(1_753_473_600 - 3600 * 30, now)).toBe("1 day ago");
  });

  it("says nothing rather than something wrong", () => {
    expect(savedAgo(0)).toBe("");
  });
});
