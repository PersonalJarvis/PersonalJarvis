import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { NoticeItem } from "@/components/agentchat/reduce";
import { IdentityNotice } from "./AgentChatPanel";

const restore = vi.hoisted(() => ({ fn: vi.fn(async () => undefined) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, fill: (text: string) => text }));
vi.mock("../data", async (original) => ({
  ...(await original<typeof import("../data")>()),
  useRestoreIdentity: () => restore.fn,
}));
afterEach(() => { cleanup(); restore.fn.mockClear(); });

const previous = { name: "Nova", title: "", description: "", focus: [] };

function notice(extra: Record<string, unknown> = {}): NoticeItem {
  return {
    type: "notice", id: "n1", kind: "proposal_resolved", text: "I am now Mail Desk - Gmail assistant.",
    agentName: "Mail Desk", agentId: "agent-1a2b3c4d", status: "applied", tsMs: 1, resolved: "",
    data: { proposal_kind: "identity", status: "applied", previous, ...extra },
  };
}

it("shows the new identity and undoes it back to the placeholder", async () => {
  render(<IdentityNotice item={notice()} />);
  expect(screen.getByText("society.chat.identity_now")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.chat.identity_undo" }));
  await waitFor(() => expect(restore.fn).toHaveBeenCalledWith("agent-1a2b3c4d", previous));
  expect(await screen.findByText("society.chat.identity_undone")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "society.chat.identity_undo" })).toBeNull();
});

it("offers no undo when the card carries nothing to restore", () => {
  render(<IdentityNotice item={notice({ previous: undefined })} />);
  expect(screen.queryByRole("button")).toBeNull();
});
