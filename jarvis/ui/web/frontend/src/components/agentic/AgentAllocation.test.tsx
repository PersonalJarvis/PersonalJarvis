import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AgentAccount } from "@/lib/agentAccountsApi";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import { AgentAllocation, type PlannedTerminal } from "./AgentAllocation";

const COPY: Record<string, string> = {
  "workspace_launcher.agents.all": "All",
  "workspace_launcher.agents.select": "Select {0}",
  "workspace_launcher.agents.decrease": "Use one fewer {0} terminal",
  "workspace_launcher.agents.count_for": "Terminal count for {0}",
  "workspace_launcher.agents.increase": "Use one more {0} terminal",
  "workspace_launcher.agents.account_for": "Subscription for {0}",
};

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => COPY[key] ?? key,
}));

const AGENTS: AgentStatus[] = [
  {
    name: "claude",
    display_name: "Claude Code",
    installed: true,
    version: "2.1",
    install_command: null,
  },
  {
    name: "codex",
    display_name: "Codex",
    installed: true,
    version: "0.142",
    install_command: null,
  },
  {
    name: "opencode",
    display_name: "OpenCode",
    installed: false,
    version: null,
    install_command: "npm install -g opencode-ai",
  },
  {
    name: "kimi",
    display_name: "Kimi Code",
    installed: false,
    version: null,
    install_command: "npm install -g @moonshot-ai/kimi-code",
  },
  {
    name: "glm",
    display_name: "GLM Coding Plan",
    installed: false,
    version: null,
    install_command: null,
  },
];

const ACCOUNTS: AgentAccount[] = [
  {
    id: "claude:default",
    platform: "claude",
    label: "Default",
    config_dir: "",
    builtin: true,
    connected: true,
    mode: "subscription",
    message: "",
    email: null,
    tier: null,
  },
  {
    id: "claude:work",
    platform: "claude",
    label: "Work seat",
    config_dir: "C:/accounts/work",
    builtin: false,
    connected: true,
    mode: "subscription",
    message: "",
    email: null,
    tier: null,
  },
];

function Harness({
  count = 5,
  accounts = [],
}: {
  count?: number;
  accounts?: AgentAccount[];
}) {
  const [planned, setPlanned] = useState<PlannedTerminal[]>(
    Array.from({ length: count }, (_, index) => ({
      agent: "",
      name: `T${index + 1}`,
    })),
  );
  return (
    <>
      <AgentAllocation
        planned={planned}
        agents={AGENTS}
        accountsFor={(platform) => (platform === "claude" ? accounts : [])}
        onPlanned={(update) => setPlanned((previous) => update(previous))}
      />
      <output data-testid="plan">{JSON.stringify(planned)}</output>
    </>
  );
}

function plan(): PlannedTerminal[] {
  return JSON.parse(
    screen.getByTestId("plan").textContent ?? "[]",
  ) as PlannedTerminal[];
}

afterEach(cleanup);

describe("AgentAllocation", () => {
  it("assigns every terminal to one agent with one click", () => {
    render(<Harness />);

    fireEvent.click(screen.getAllByRole("button", { name: "All" })[0]);

    expect(plan().map((pane) => pane.agent)).toEqual([
      "claude",
      "claude",
      "claude",
      "claude",
      "claude",
    ]);
    expect(screen.getByText("5 / 5")).toBeTruthy();
  });

  it("turns a typed count into a batch split without per-terminal editing", () => {
    render(<Harness count={17} />);
    fireEvent.click(screen.getAllByRole("button", { name: "All" })[0]);

    fireEvent.change(
      screen.getByRole("spinbutton", { name: "Terminal count for Codex" }),
      { target: { value: "7" } },
    );

    const agents = plan().map((pane) => pane.agent);
    expect(agents.filter((agent) => agent === "claude")).toHaveLength(10);
    expect(agents.filter((agent) => agent === "codex")).toHaveLength(7);
  });

  it("uses local product marks and keeps the selected agent logo visible", () => {
    render(<Harness />);

    // `data-logo` rather than the <img>: a single-colour mark is drawn as a
    // CSS mask so it can follow the theme, and jsdom does not model masks.
    const claudeMark = screen.getByTestId("agent-mark-claude");
    expect(claudeMark.getAttribute("data-logo")).toBe(
      "/provider-logos/claude.svg",
    );
    expect(
      screen.getByTestId("agent-mark-codex").getAttribute("data-logo"),
    ).toBe("/provider-logos/openai.svg");
    expect(
      screen
        .getByTestId("agent-mark-opencode")
        .querySelector("img")
        ?.getAttribute("src"),
    ).toBe("/agent-logos/opencode.svg");
    expect(
      screen
        .getByTestId("agent-mark-kimi")
        .querySelector("img")
        ?.getAttribute("src"),
    ).toBe("/agent-logos/kimi.svg");
    expect(
      screen
        .getByTestId("agent-mark-glm")
        .querySelector("img")
        ?.getAttribute("src"),
    ).toBe("/agent-logos/zai.svg");

    fireEvent.click(screen.getAllByRole("button", { name: "All" })[0]);
    expect(claudeMark.getAttribute("data-logo")).toBe(
      "/provider-logos/claude.svg",
    );
  });

  it("presents a wide keyboard-friendly count field without native spinners", () => {
    render(<Harness count={17} />);

    const input = screen.getByRole("spinbutton", {
      name: "Terminal count for Codex",
    }) as HTMLInputElement;
    expect(input.inputMode).toBe("numeric");
    expect(input.step).toBe("1");
    expect(input.className).toContain("[appearance:textfield]");
    expect(input.className).toContain(
      "[&::-webkit-inner-spin-button]:appearance-none",
    );
    expect(input.value).toBe("0");
    expect(screen.getByTestId("allocation-stepper-codex").textContent).toMatch(
      /\/\s*17/,
    );
  });

  it("splits every slot evenly across installed agents", () => {
    render(<Harness />);

    fireEvent.click(screen.getByText("workspace_launcher.agents.split_evenly"));

    expect(plan().map((pane) => pane.agent)).toEqual([
      "claude",
      "claude",
      "claude",
      "codex",
      "codex",
    ]);
  });

  it("can place one of each and leave the remaining slots visibly open", () => {
    render(<Harness />);

    fireEvent.click(screen.getByText("workspace_launcher.agents.one_each"));

    expect(plan().map((pane) => pane.agent)).toEqual([
      "claude",
      "codex",
      "",
      "",
      "",
    ]);
    expect(screen.getByText("2 / 5")).toBeTruthy();
  });

  it("applies one selected subscription to every terminal of an agent", async () => {
    render(<Harness count={3} accounts={ACCOUNTS} />);
    fireEvent.click(screen.getAllByRole("button", { name: "All" })[0]);

    fireEvent.click(
      screen.getByRole("combobox", { name: "Subscription for Claude Code" }),
    );
    fireEvent.click(await screen.findByText("Work seat"));

    expect(plan().map((pane) => pane.account)).toEqual([
      "claude:work",
      "claude:work",
      "claude:work",
    ]);
  });

  it("shows unavailable agents without allowing them to consume slots", () => {
    render(<Harness />);

    expect(
      (
        screen.getByRole("spinbutton", {
          name: "Terminal count for OpenCode",
        }) as HTMLInputElement
      ).disabled,
    ).toBe(true);
  });
});
