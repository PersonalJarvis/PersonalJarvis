import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { NoticeItem } from "@/components/agentchat/reduce";
import { NoticeLine } from "./AgentChatPanel";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, fill: (text: string) => text }));
afterEach(cleanup);

function notice(kind: string, data: Record<string, unknown>, text = "English from the runtime"): NoticeItem {
  return {
    type: "notice", id: "n1", kind, text, agentName: "", agentId: "", status: "", tsMs: 1, resolved: "",
    data: { kind, ...data },
  };
}

it("says in the person's language that a runtime answer was cut off", () => {
  render(<NoticeLine item={notice("stop_reason", { stop_reason: "max_tokens", turn_id: "t1" })} />);
  const line = screen.getByTestId("stop-reason-notice");
  expect(line.textContent).toBe("society.chat.stop_reason_max_tokens");
  expect(line.getAttribute("role")).toBe("note");
});

it("falls back to the runtime's sentence for a stop reason it does not know", () => {
  render(<NoticeLine item={notice("stop_reason", { stop_reason: "something_new" })} />);
  expect(screen.getByTestId("stop-reason-notice").textContent).toBe("English from the runtime");
});

it("names the runtime a routine run did not use", () => {
  render(<NoticeLine item={notice("routine_runtime_fallback", { runtime: "hermes" })} />);
  expect(screen.getByTestId("routine-runtime-fallback").textContent).toBe("society.runtime.routine_fallback");
});

it("announces a runtime setup, the one-time Hermes prepare included", () => {
  render(<NoticeLine item={notice("runtime_setup", { runtime: "hermes", turn_id: "t1" })} />);
  expect(screen.getByTestId("runtime-setup-notice").textContent).toBe("society.runtime.setting_up_chat");
});
