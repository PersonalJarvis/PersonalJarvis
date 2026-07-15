import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUiLanguage } from "@/i18n";
import { SkillCreateDialog } from "@/views/SkillCreateDialog";

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function renderDialog(onClose = vi.fn(), onCreated = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <SkillCreateDialog
        open
        onClose={onClose}
        onCreated={onCreated}
      />
    </QueryClientProvider>,
  );
  return { onClose, onCreated };
}

beforeEach(() => {
  setUiLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("SkillCreateDialog creation routes", () => {
  it("commits an AI-generated skill through the draft-enforcing creator route", async () => {
    const draft = {
      name: "AI Routine",
      description: "Original description",
      category: "productivity",
      tags: ["routine"],
      triggers: [
        { type: "schedule", cron: "0 8 * * *" },
        { type: "voice", pattern: "start routine" },
      ],
      requires_tools: ["calendar.read"],
      risk_policy: { default_tier: "ask" },
      body: "## AI Routine\n\nRun the reviewed routine.\n",
      questions: [],
      assumptions: ["A calendar is connected."],
      test_prompts: ["Start my routine"],
      frontmatter: { state: "active" },
    };
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/skills/creator/draft") {
          return response({
            draft,
            skill_md: "---\nstate: draft\n---\n",
            validation: {
              ok: true,
              state: "validated",
              errors: [],
              warnings: [],
            },
            brain_used: true,
          });
        }
        if (url === "/api/skills/creator/commit") {
          return response({ name: "Reviewed Routine", state: "draft" });
        }
        throw new Error(`Unexpected ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onClose, onCreated } = renderDialog();

    fireEvent.change(
      screen.getByPlaceholderText(/Summarize my open browser tabs/i),
      { target: { value: "Build a safe morning routine" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Write the whole skill with AI" }),
    );

    await screen.findByDisplayValue("AI Routine");
    fireEvent.change(screen.getByPlaceholderText("Skill name"), {
      target: { value: "Reviewed Routine" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("One sentence about what it does"),
      { target: { value: "Reviewed description" } },
    );
    fireEvent.change(screen.getByPlaceholderText(/## My Skill/), {
      target: { value: "## Reviewed Routine\n\nRun only reviewed steps.\n" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("e.g. start the morning routine"),
      { target: { value: "run reviewed routine" } },
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Create skill" }),
    );

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("Reviewed Routine"));
    expect(onClose).toHaveBeenCalledOnce();

    const draftCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/skills/creator/draft",
    );
    expect(draftCall?.[1]).toEqual(expect.objectContaining({ method: "POST" }));
    const commitCall = fetchMock.mock.calls.find(
      ([url]) => String(url) === "/api/skills/creator/commit",
    );
    expect(commitCall).toBeDefined();
    expect(commitCall?.[1]).toEqual(expect.objectContaining({ method: "POST" }));
    const commitBody = JSON.parse(
      String((commitCall?.[1] as RequestInit | undefined)?.body),
    );
    expect(commitBody.draft).toEqual(
      expect.objectContaining({
        name: "Reviewed Routine",
        description: "Reviewed description",
        body: "## Reviewed Routine\n\nRun only reviewed steps.",
        tags: ["routine"],
        requires_tools: ["calendar.read"],
        risk_policy: { default_tier: "ask" },
        triggers: [
          { type: "schedule", cron: "0 8 * * *" },
          { type: "voice", pattern: "run reviewed routine" },
        ],
      }),
    );
    expect(
      fetchMock.mock.calls.some(([url]) => String(url) === "/api/skills"),
    ).toBe(false);
  });

  it("keeps manual creation on the manual endpoint", async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/skills") {
          return response({ name: "Manual Skill", state: "validated" });
        }
        throw new Error(`Unexpected ${init?.method ?? "GET"} ${url}`);
      },
    );
    vi.stubGlobal("fetch", fetchMock);
    const { onCreated } = renderDialog();

    fireEvent.change(screen.getByPlaceholderText("Skill name"), {
      target: { value: "Manual Skill" },
    });
    fireEvent.change(screen.getByPlaceholderText(/## My Skill/), {
      target: { value: "## Manual Skill\n\nFollow the manual instructions.\n" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create skill" }),
    );

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("Manual Skill"));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/skills",
      expect.objectContaining({ method: "POST" }),
    );
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).startsWith("/api/skills/creator/"),
      ),
    ).toBe(false);
  });
});
