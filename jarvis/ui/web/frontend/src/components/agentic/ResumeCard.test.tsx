import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResumeCard, resumeSummary, savedAgo } from "./ResumeCard";
import type {
  ResumeOffer,
  ResumeTerminalOffer,
  ResumeWorkspaceOffer,
} from "@/lib/agenticIdeApi";

function pane(
  name: string,
  extra: Partial<ResumeTerminalOffer> = {},
): ResumeTerminalOffer {
  return {
    key: name.toLowerCase(),
    name,
    agent: "claude",
    display_name: "Claude Code",
    column: 0,
    slot: 0,
    available: true,
    resumable: true,
    prompts_sent: 1,
    ...extra,
  };
}

const WORKSPACE: ResumeWorkspaceOffer = {
  session_id: "ide_1",
  folder: "/home/ruben/code/project",
  folder_name: "project",
  name: "",
  folder_exists: true,
  available: true,
  resumable_count: 1,
  saved_at: 1_753_473_600,
  in_last_session: true,
  terminals: [pane("Alex"), pane("Blake", { resumable: false, prompts_sent: 0 })],
};

const OFFER: ResumeOffer = {
  available: true,
  saved_at: 1_753_473_600,
  workspace_count: 1,
  terminal_count: 2,
  resumable_count: 1,
  earlier_count: 0,
  workspaces: [WORKSPACE],
};

afterEach(cleanup);

/** The project has no jest-dom matchers, so buttons are inspected directly. */
function button(testId: string): HTMLButtonElement {
  return screen.getByTestId(testId) as HTMLButtonElement;
}

describe("ResumeCard", () => {
  it("names the workspace and its panes", () => {
    render(
      <ResumeCard offer={OFFER} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByText("project")).toBeTruthy();
    expect(screen.getByText("Alex")).toBeTruthy();
    expect(screen.getByText("Blake")).toBeTruthy();
  });

  it("lists EVERY workspace that was open, not just one", () => {
    const two: ResumeOffer = {
      ...OFFER,
      workspace_count: 2,
      terminal_count: 4,
      workspaces: [
        WORKSPACE,
        {
          ...WORKSPACE,
          session_id: "ide_2",
          folder: "/home/ruben/code/other",
          folder_name: "other",
        },
      ],
    };
    render(
      <ResumeCard offer={two} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByTestId("resume-workspace-project")).toBeTruthy();
    expect(screen.getByTestId("resume-workspace-other")).toBeTruthy();
  });

  it("says which conversations will NOT come back, before the click", () => {
    // The honesty requirement: an empty pane and a continued one look identical
    // until the agent is asked a follow-up question, so the difference has to be
    // visible while the choice is still being made.
    render(
      <ResumeCard offer={OFFER} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByTestId("resume-pane-blake").textContent).toMatch(
      /starts fresh/i,
    );
    expect(screen.getByTestId("resume-pane-alex").textContent).not.toMatch(
      /starts fresh/i,
    );
  });

  it("marks a pane whose coding CLI is gone from this machine", () => {
    const offer: ResumeOffer = {
      ...OFFER,
      workspaces: [
        {
          ...WORKSPACE,
          terminals: [pane("Alex"), pane("Blake", { available: false })],
        },
      ],
    };
    render(
      <ResumeCard offer={offer} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByTestId("resume-pane-blake").textContent).toMatch(
      /not installed here/i,
    );
  });

  it("summarises the call-signs once there are too many to read", () => {
    // A hundred chips is wallpaper, not information.
    const many = Array.from({ length: 30 }, (_, i) => pane(`T${i}`));
    const offer: ResumeOffer = {
      ...OFFER,
      terminal_count: 30,
      workspaces: [{ ...WORKSPACE, terminals: many, resumable_count: 30 }],
    };
    render(
      <ResumeCard offer={offer} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(screen.getByTestId("resume-pane-t0")).toBeTruthy();
    expect(screen.queryByTestId("resume-pane-t29")).toBeNull();
    expect(screen.getByTestId("resume-more-project").textContent).toMatch(/\+22 more/);
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

  it("names the workspace whose folder is gone without hiding the others", () => {
    const offer: ResumeOffer = {
      ...OFFER,
      workspace_count: 2,
      terminal_count: 4,
      workspaces: [
        WORKSPACE,
        {
          ...WORKSPACE,
          session_id: "ide_2",
          folder: "/home/ruben/code/deleted",
          folder_name: "deleted",
          folder_exists: false,
          available: false,
        },
      ],
    };
    render(
      <ResumeCard offer={offer} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    // The good one is still resumable, so the button stays usable.
    expect(button("resume-all").disabled).toBe(false);
    expect(screen.getByText(/moved or deleted/i)).toBeTruthy();
  });

  it("cannot be resumed when nothing at all can come back", () => {
    const gone: ResumeOffer = {
      ...OFFER,
      available: false,
      workspaces: [{ ...WORKSPACE, folder_exists: false, available: false }],
    };
    render(
      <ResumeCard offer={gone} busy={false} onResume={vi.fn()} onDismiss={vi.fn()} />,
    );
    expect(button("resume-all").disabled).toBe(true);
    // ...but "start fresh" still works, or the card could never be cleared away.
    expect(button("resume-dismiss").disabled).toBe(false);
  });

  it("locks both buttons while a resume is in flight", () => {
    render(<ResumeCard offer={OFFER} busy onResume={vi.fn()} onDismiss={vi.fn()} />);
    expect(button("resume-all").disabled).toBe(true);
    expect(button("resume-dismiss").disabled).toBe(true);
  });
});

describe("resumeSummary", () => {
  it("distinguishes all, some and none", () => {
    expect(
      resumeSummary({ ...OFFER, resumable_count: 2 }),
    ).toMatch(/each continuing/i);
    expect(resumeSummary(OFFER)).toMatch(/1 continue/i);
    expect(resumeSummary({ ...OFFER, resumable_count: 0 })).toMatch(
      /none of their conversations/i,
    );
  });

  it("counts folders as well as terminals", () => {
    expect(resumeSummary({ ...OFFER, workspace_count: 3, terminal_count: 9 })).toMatch(
      /3 folders, 9 terminals/,
    );
  });

  it("leads with the reason nothing can be reopened", () => {
    expect(
      resumeSummary({
        ...OFFER,
        available: false,
        workspaces: [{ ...WORKSPACE, folder_exists: false, available: false }],
      }),
    ).toMatch(/no longer on this machine/i);
    expect(
      resumeSummary({
        ...OFFER,
        available: false,
        workspaces: [{ ...WORKSPACE, available: false }],
      }),
    ).toMatch(/not installed/i);
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
