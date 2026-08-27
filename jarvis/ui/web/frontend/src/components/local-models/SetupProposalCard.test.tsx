import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  fill: (template: string, vars: Record<string, string | number>) =>
    `${template}${Object.values(vars).join("|")}`,
}));

const { switchBrainProvider } = vi.hoisted(() => ({
  switchBrainProvider: vi.fn(async (_id: string) => undefined),
}));
vi.mock("@/hooks/useProviders", () => ({ switchBrainProvider }));

import { BrainSwitchCard, SetupProposalCard } from "./SetupProposalCard";
import { proposalHash, type Proposal } from "./assistantProposal";

const PROPOSAL: Proposal = {
  version: 1,
  steps: [
    { id: "s1", kind: "pull", model: "qwen3.5:8b", size_gb: 5.2, proven: "proven", label: "Download Qwen" },
    { id: "s2", kind: "set_role", role: "chat", model: "qwen3.5:8b", proven: "new_little_tested", label: "Use for Chat" },
    { id: "s3", kind: "test", proven: "stale", label: "Test it" },
  ],
  brain_switch: null,
  notes: ["Nothing is deleted."],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SetupProposalCard", () => {
  it("lists every step with its badge and confirms all of them in one click", async () => {
    const onConfirm = vi.fn(async () => undefined);
    render(<SetupProposalCard proposal={PROPOSAL} onConfirm={onConfirm} />);

    expect(screen.getByTestId("proposal-badge-proven")).toBeDefined();
    expect(screen.getByTestId("proposal-badge-new_little_tested")).toBeDefined();
    expect(screen.getByTestId("proposal-badge-stale")).toBeDefined();
    expect(screen.getByText("5.2 GB")).toBeDefined();
    expect(screen.getByText("Nothing is deleted.")).toBeDefined();

    fireEvent.click(screen.getByTestId("proposal-confirm"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    const [steps, message] = onConfirm.mock.calls[0] as unknown as [Proposal["steps"], string];
    expect(steps.map((s) => s.id)).toEqual(["s1", "s2", "s3"]);
    expect(message).toBe(`Execute steps: s1, s2, s3 (proposal v1, hash ${proposalHash(PROPOSAL)})`);
  });

  it("leaves an unticked step out of the confirmation", async () => {
    const onConfirm = vi.fn(async () => undefined);
    render(<SetupProposalCard proposal={PROPOSAL} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByTestId("proposal-step-s2"));
    expect(screen.getByTestId("proposal-confirm").textContent).toContain(
      "local_models.assistant.confirm_some",
    );
    fireEvent.click(screen.getByTestId("proposal-confirm"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
    const [steps, message] = onConfirm.mock.calls[0] as unknown as [Proposal["steps"], string];
    expect(steps.map((s) => s.id)).toEqual(["s1", "s3"]);
    expect(message.startsWith("Execute steps: s1, s3 ")).toBe(true);
  });

  it("cannot confirm nothing, and shows the receipt once confirmed", () => {
    const onConfirm = vi.fn(async () => undefined);
    const { rerender } = render(<SetupProposalCard proposal={PROPOSAL} onConfirm={onConfirm} />);
    for (const id of ["s1", "s2", "s3"]) fireEvent.click(screen.getByTestId(`proposal-step-${id}`));
    expect((screen.getByTestId("proposal-confirm") as HTMLButtonElement).disabled).toBe(true);

    rerender(
      <SetupProposalCard proposal={PROPOSAL} onConfirm={onConfirm} confirmedHash={proposalHash(PROPOSAL)} />,
    );
    expect(screen.queryByTestId("proposal-confirm")).toBeNull();
    expect(screen.getByTestId("proposal-confirmed")).toBeDefined();
  });
});

describe("BrainSwitchCard", () => {
  it("switches the brain only on the user's click", async () => {
    render(<BrainSwitchCard provider="ollama" why="Runs locally." serverLabel="Ollama" />);
    expect(switchBrainProvider).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("brain-switch-run"));
    await waitFor(() => expect(switchBrainProvider).toHaveBeenCalledWith("ollama"));
    await waitFor(() => expect(screen.queryByTestId("brain-switch-run")).toBeNull());
    expect(screen.getByText(/brain_switch_done/)).toBeDefined();
  });
});
