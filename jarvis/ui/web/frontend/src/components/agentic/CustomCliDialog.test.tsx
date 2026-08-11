import { useState } from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentAccount } from "@/lib/agentAccountsApi";
import type { AgentStatus } from "@/lib/agenticIdeApi";
import { AgentAllocation, type PlannedTerminal } from "./AgentAllocation";
import { CustomCliDialog, runsThroughShell } from "./CustomCliDialog";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/lib/workspaceClisApi", () => ({
  fetchCustomClis: vi.fn(),
  createCustomCli: vi.fn(),
  updateCustomCli: vi.fn(),
  deleteCustomCli: vi.fn(),
  uploadCustomCliLogo: vi.fn(),
  removeCustomCliLogo: vi.fn(),
}));

import * as api from "@/lib/workspaceClisApi";

/** No jest-dom in this repo — assertions read the elements directly. */
function field(testId: string): HTMLInputElement {
  return screen.getByTestId(testId) as HTMLInputElement;
}

function text(testId: string): string {
  return screen.getByTestId(testId).textContent ?? "";
}

const STORED: api.CustomCli = {
  id: "antigravity",
  display_name: "Antigravity",
  command: "agy",
  description: "Google's terminal coding CLI.",
  file_reference: "quoted",
  logo_url: "",
  runs_through_shell: false,
  binary: "agy",
};

const AGENTS: AgentStatus[] = [
  {
    name: "claude",
    display_name: "Claude Code",
    installed: true,
    version: "2.1",
    install_command: null,
  },
  {
    name: "antigravity",
    display_name: "Antigravity",
    installed: true,
    version: null,
    install_command: null,
    custom: true,
    description: "Google's terminal coding CLI.",
  },
];

function Harness({ count = 4 }: { count?: number }) {
  const [planned, setPlanned] = useState<PlannedTerminal[]>(
    Array.from({ length: count }, (_, index) => ({
      agent: index === 0 ? "antigravity" : "",
      name: `T${index + 1}`,
    })),
  );
  const [reloads, setReloads] = useState(0);
  return (
    <>
      <AgentAllocation
        planned={planned}
        agents={AGENTS}
        accountsFor={(): AgentAccount[] => []}
        onPlanned={(update) => setPlanned((previous) => update(previous))}
        onAgentsChanged={() => setReloads((n) => n + 1)}
      />
      <output data-testid="plan">{JSON.stringify(planned)}</output>
      <output data-testid="reloads">{reloads}</output>
    </>
  );
}

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  // jsdom ships no object-URL implementation, and the logo preview asks for
  // one the moment a file is picked.
  Object.assign(URL, {
    createObjectURL: vi.fn(() => "blob:preview"),
    revokeObjectURL: vi.fn(),
  });
});

describe("recognising shell source", () => {
  // Mirrors custom_clis.needs_shell — the two must agree, because the backend's
  // answer decides how the pane starts and this one decides what the user was
  // told would happen.
  it.each([
    ["agy", false],
    ["npx -y some-cli", false],
    ["agy | tee log.txt", true],
    ["agy && echo done", true],
    ["FOO=1 agy", true],
    ["agy --model $MODEL", true],
  ])("%s", (command, expected) => {
    expect(runsThroughShell(command)).toBe(expected);
  });
});

describe("CustomCliDialog", () => {
  it("cannot be saved without both a name and a command", () => {
    render(
      <CustomCliDialog open onOpenChange={() => {}} onSaved={() => {}} />,
    );
    const save = screen.getByTestId("custom-cli-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);

    fireEvent.change(field("custom-cli-name"), {
      target: { value: "Antigravity" },
    });
    expect(save.disabled).toBe(true);

    fireEvent.change(screen.getByTestId("custom-cli-command"), {
      target: { value: "agy" },
    });
    expect(save.disabled).toBe(false);
  });

  it("warns while typing that a shell will wrap the command", () => {
    render(
      <CustomCliDialog open onOpenChange={() => {}} onSaved={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("custom-cli-command"), {
      target: { value: "agy" },
    });
    expect(screen.getByText("custom_cli.command_hint")).toBeTruthy();

    fireEvent.change(screen.getByTestId("custom-cli-command"), {
      target: { value: "agy | tee log.txt" },
    });
    expect(screen.getByText("custom_cli.command_shell_hint")).toBeTruthy();
  });

  it("creates the entry and reports it", async () => {
    vi.mocked(api.createCustomCli).mockResolvedValue(STORED);
    const onSaved = vi.fn();
    render(
      <CustomCliDialog open onOpenChange={() => {}} onSaved={onSaved} />,
    );

    fireEvent.change(screen.getByTestId("custom-cli-name"), {
      target: { value: "Antigravity" },
    });
    fireEvent.change(screen.getByTestId("custom-cli-command"), {
      target: { value: "agy" },
    });
    fireEvent.click(screen.getByTestId("custom-cli-save"));

    await waitFor(() => expect(api.createCustomCli).toHaveBeenCalled());
    expect(api.createCustomCli).toHaveBeenCalledWith({
      display_name: "Antigravity",
      command: "agy",
      description: "",
      file_reference: "quoted",
    });
    expect(onSaved).toHaveBeenCalledWith(STORED);
  });

  it("shows the server's own complaint instead of a status code", async () => {
    vi.mocked(api.createCustomCli).mockRejectedValue(
      new Error("Give the command that starts this CLI."),
    );
    render(
      <CustomCliDialog open onOpenChange={() => {}} onSaved={() => {}} />,
    );
    fireEvent.change(screen.getByTestId("custom-cli-name"), {
      target: { value: "Antigravity" },
    });
    fireEvent.change(screen.getByTestId("custom-cli-command"), {
      target: { value: "agy" },
    });
    fireEvent.click(screen.getByTestId("custom-cli-save"));

    await waitFor(() =>
      expect(text("custom-cli-error")).toContain(
        "Give the command that starts this CLI.",
      ),
    );
  });

  it("uploads a picked logo after the entry exists", async () => {
    // The id the file is named after is assigned on creation, so a logo cannot
    // be stored before the entry it belongs to.
    vi.mocked(api.createCustomCli).mockResolvedValue(STORED);
    vi.mocked(api.uploadCustomCliLogo).mockResolvedValue({
      ...STORED,
      logo_url: "/api/workspace-clis/antigravity/logo",
    });
    render(
      <CustomCliDialog open onOpenChange={() => {}} onSaved={() => {}} />,
    );

    fireEvent.change(screen.getByTestId("custom-cli-name"), {
      target: { value: "Antigravity" },
    });
    fireEvent.change(screen.getByTestId("custom-cli-command"), {
      target: { value: "agy" },
    });
    const file = new File(["<svg />"], "mark.svg", { type: "image/svg+xml" });
    fireEvent.change(screen.getByTestId("custom-cli-logo-input"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByTestId("custom-cli-save"));

    await waitFor(() => expect(api.uploadCustomCliLogo).toHaveBeenCalled());
    expect(api.uploadCustomCliLogo).toHaveBeenCalledWith("antigravity", file);
  });

  it("fills its fields from the entry being edited", () => {
    render(
      <CustomCliDialog
        open
        onOpenChange={() => {}}
        editing={{ ...STORED, file_reference: "at" }}
        onSaved={() => {}}
      />,
    );
    expect(field("custom-cli-name").value).toBe("Antigravity");
    expect(field("custom-cli-command").value).toBe("agy");
    expect(field("custom-cli-at-reference").checked).toBe(true);
  });
});

describe("AgentAllocation with a CLI of your own", () => {
  it("offers editing only on the rows that have something to edit", () => {
    render(<Harness />);
    expect(screen.getByTestId("custom-cli-edit-antigravity")).toBeTruthy();
    expect(screen.queryByTestId("custom-cli-edit-claude")).toBeNull();
  });

  it("loads the stored record before opening the editor", async () => {
    // `/agents` carries what a picker shows; the dialog needs the command.
    vi.mocked(api.fetchCustomClis).mockResolvedValue({
      clis: [STORED],
      max_name_length: 60,
      max_command_length: 500,
      max_logo_bytes: 1024,
      logo_extensions: [".svg"],
    });
    render(<Harness />);
    fireEvent.click(screen.getByTestId("custom-cli-edit-antigravity"));

    await waitFor(() =>
      expect(field("custom-cli-command").value).toBe("agy"),
    );
  });

  it("frees the slots a removed CLI held", async () => {
    vi.mocked(api.deleteCustomCli).mockResolvedValue({ ok: true });
    render(<Harness />);
    expect(JSON.parse(screen.getByTestId("plan").textContent ?? "[]")[0]).toEqual(
      { agent: "antigravity", name: "T1" },
    );

    fireEvent.click(screen.getByTestId("custom-cli-remove-antigravity"));

    await waitFor(() =>
      expect(
        JSON.parse(screen.getByTestId("plan").textContent ?? "[]")[0],
      ).toEqual({ agent: "", name: "T1" }),
    );
    // And the list is re-read, or the entry stays on screen after removal.
    expect(text("reloads")).toBe("1");
  });

  it("reports a failed removal instead of pretending it worked", async () => {
    vi.mocked(api.deleteCustomCli).mockRejectedValue(new Error("store is busy"));
    render(<Harness />);
    fireEvent.click(screen.getByTestId("custom-cli-remove-antigravity"));

    await waitFor(() =>
      expect(text("custom-cli-list-error")).toContain("store is busy"),
    );
    expect(JSON.parse(screen.getByTestId("plan").textContent ?? "[]")[0]).toEqual(
      { agent: "antigravity", name: "T1" },
    );
  });
});
