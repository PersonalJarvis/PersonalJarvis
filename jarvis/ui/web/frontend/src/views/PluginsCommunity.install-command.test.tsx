import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  InstallConsentDialog,
  SkillInstallConsentDialog,
  type CommunityPluginWire,
  type CommunitySkillWire,
} from "@/views/PluginsCommunity";

// The command shown here and the command shown on the storefront have to be
// the same string — a store that advertises a line the CLI does not accept is
// worse than one that shows none. installStandard.test.ts pins the formula;
// this pins that the dialog actually puts it in front of the reader.

const SKILL: CommunitySkillWire = {
  name: "three-point-check",
  title: "Three Point Check",
  description: "Summarize any topic in three bullets",
  publisher: "octocat",
  version: "1.0.0",
  categories: ["productivity"],
  source_url: "https://github.com/PersonalJarvis/marketplace",
  raw_url: "https://raw.example/skills/three-point-check/SKILL.md",
  installed: false,
};

const PLUGIN: CommunityPluginWire = {
  name: "todo-fox",
  valid: true,
  publisher: "octocat",
  version: "1.0.0",
  id: "todo-fox",
  display_name: "Todo Fox",
  description: "A todo list connector",
  category: "productivity",
  installed: false,
};

function stubFetch() {
  // ContentsPanel fetches the published files; this test is about the command
  // line, so every request answers empty rather than failing the render.
  const fetchMock = vi.fn(
    async () =>
      ({ ok: false, status: 404, json: async () => ({}) }) as Response,
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

/** The dialog carries a ContentsPanel, which is a query — so it needs a client. */
function renderDialog(ui: ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the install command inside the consent dialog", () => {
  it("shows the storefront's command for a skill", () => {
    stubFetch();
    renderDialog(
      <SkillInstallConsentDialog
        skill={SKILL}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    expect(
      screen.getByText("jarvis marketplace install three-point-check", {
        exact: false,
      }),
    ).toBeDefined();
  });

  it("shows the storefront's command for a plugin", () => {
    stubFetch();
    renderDialog(
      <InstallConsentDialog
        plugin={PLUGIN}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    expect(
      screen.getByText("jarvis marketplace install todo-fox", { exact: false }),
    ).toBeDefined();
  });

  // A skill published from GitHub can also be installed with `npx skills add`,
  // which puts it into Claude Code, Cursor, Codex, and the rest. The dialog has
  // to offer that line too, or the store hides half of what the entry can do.
  it("offers the skills.sh command next to the Jarvis one", () => {
    stubFetch();
    renderDialog(
      <SkillInstallConsentDialog
        skill={SKILL}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("tab", { name: "skills.sh" }));

    expect(
      screen.getByText(
        "npx skills add PersonalJarvis/marketplace --skill three-point-check",
        { exact: false },
      ),
    ).toBeDefined();
  });

  // A plugin has an MCP server and a sign-in flow; `npx skills` installs
  // instruction files. A tab strip there would advertise a broken install.
  it("keeps a plugin on the Jarvis command alone", () => {
    stubFetch();
    renderDialog(
      <InstallConsentDialog
        plugin={PLUGIN}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    expect(screen.queryByRole("tab", { name: "skills.sh" })).toBeNull();
  });

  it("copies the command, and says it did", async () => {
    stubFetch();
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    renderDialog(
      <SkillInstallConsentDialog
        skill={SKILL}
        isPending={false}
        errorMessage={null}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );

    const dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: /copy the install command/i,
      }),
    );

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "jarvis marketplace install three-point-check",
      ),
    );
    // The confirmation is the whole point of a copy button: without it a
    // reader cannot tell a working button from a dead one.
    expect(await screen.findByText("Copied")).toBeDefined();
  });
});
