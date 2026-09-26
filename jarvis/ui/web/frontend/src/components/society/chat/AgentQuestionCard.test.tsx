import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { NoticeItem } from "@/components/agentchat/reduce";
import en from "@/i18n/locales/society/en.json";
import { AgentQuestionCard } from "./AgentQuestionCard";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key.split(".").reduce<unknown>(
    (value, part) => (value as Record<string, unknown>)?.[part], en,
  ) ?? key,
}));

const question: NoticeItem = {
  type: "notice", id: "n-1", kind: "agent_question", agentName: "Scout", agentId: "scout",
  text: "Which format?", status: "", tsMs: 1, resolved: "",
  data: {
    question_id: "q1", question: "Which format?", deadline_ms: Date.now() + 300_000,
    options: [
      { label: "PDF", description: "Easy to share" },
      { label: "HTML", description: "Easy to update" },
    ],
    recommended_index: 1, recommendation_reason: "The user will edit it again.",
  },
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

test("shows recommendation and submits the selected option", async () => {
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  render(<AgentQuestionCard item={question} sessionId="society:scout" />);
  expect(screen.getByText("Recommended")).toBeTruthy();
  expect(screen.getByText(/Default in/)).toBeTruthy();
  fireEvent.click(screen.getByText("PDF"));
  fireEvent.click(screen.getByText("Submit answer"));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(fetcher.mock.calls[0]![0]).toContain("/questions/q1");
  expect(JSON.parse(String(fetcher.mock.calls[0]![1]!.body))).toEqual({ selected_index: 0 });
});

test("custom text replaces the recommendation and resolved cards cannot resubmit", async () => {
  const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, status: 200 }));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<AgentQuestionCard item={question} sessionId="society:scout" />);
  fireEvent.change(screen.getByLabelText("Your own answer"), { target: { value: "Markdown" } });
  fireEvent.click(screen.getByText("Submit answer"));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  expect(JSON.parse(String(fetcher.mock.calls[0]![1]!.body))).toEqual({ custom_text: "Markdown" });
  view.rerender(<AgentQuestionCard item={{ ...question, resolved: "timeout", data: { ...question.data, answer: "HTML" } }} sessionId="society:scout" />);
  expect(screen.getByText(/Automatically selected/)).toBeTruthy();
  expect(screen.queryByText("Submit answer")).toBeNull();
});
