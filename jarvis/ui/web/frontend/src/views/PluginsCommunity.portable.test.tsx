import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SkillInstallConsentDialog,
  type CommunitySkillWire,
} from "@/views/PluginsCommunity";

// A portable skill was written for the open Agent Skills format, not for this
// app. The dialog has to say so before the install: it explains both the extra
// install command and why settings meant for another agent are ignored.

const PORTABLE: CommunitySkillWire = {
  name: "three-point-check",
  title: "Three Point Check",
  description: "Summarize any topic in three bullets",
  publisher: "octocat",
  version: "1.0.0",
  categories: [],
  source_url: "https://github.com/octocat/skills",
  raw_url:
    "https://raw.githubusercontent.com/octocat/skills/main/skills/three-point-check/SKILL.md",
  installed: false,
  flavor: "portable",
  compatible_agents: ["Claude Code", "Cursor"],
};

function stubFetch() {
  const fetchMock = vi.fn(
    async () =>
      ({ ok: false, status: 404, json: async () => ({}) }) as Response,
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

function renderDialog(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("a portable community skill", () => {
  it("names the agents it also runs in", () => {
    stubFetch();
    renderDialog(
      <SkillInstallConsentDialog
        skill={PORTABLE}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    expect(
      screen.getByText(/also runs in Claude Code, Cursor/i),
    ).toBeDefined();
  });

  // Absence is not "unknown": every entry published before the split was a
  // Jarvis skill, so a missing flavor must stay quiet.
  it("says nothing for a skill without a flavor", () => {
    stubFetch();
    const { flavor: _flavor, compatible_agents: _agents, ...plain } = PORTABLE;
    renderDialog(
      <SkillInstallConsentDialog
        skill={plain}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    expect(screen.queryByText(/also runs in/i)).toBeNull();
  });
});
