import { describe, expect, it, vi } from "vitest";

import type { SplitAgentChoice } from "@/components/agentic/AgentPicker";
import {
  chatStartableAgents,
  createNewPaneChatStore,
  firstStartableAgent,
  seedDraft,
  type NewPaneRequest,
} from "@/store/newPaneChat";

/**
 * The new chat, before it has an agent behind it.
 *
 * The one thing worth pinning here is the ORDER: the picks are chosen while
 * nothing is running, and the first message is what starts the pane on them.
 * Get that backwards and the picker is decoration — a pill saying "Opus" over
 * a process that was started on whatever the CLI defaults to, which is exactly
 * the bug this replaced.
 */

const CLAUDE: SplitAgentChoice = {
  name: "claude",
  displayName: "Claude Code",
  installed: true,
  kind: "cli",
  picks: {
    models: [
      { id: "claude-opus-5", label: "Claude Opus 5" },
      { id: "claude-sonnet-5", label: "Claude Sonnet 5" },
    ],
    defaultModel: "",
    effortLevels: ["low", "medium", "high"],
    defaultEffort: "high",
    permissionModes: [
      { id: "default", label: "Ask before acting", description: "…" },
      { id: "plan", label: "Plan", description: "…" },
    ],
    defaultPermissionMode: "",
  },
};

const CODEX: SplitAgentChoice = {
  name: "codex",
  displayName: "Codex",
  installed: true,
  kind: "cli",
  picks: {
    models: [{ id: "gpt-5.6-sol", label: "GPT-5.6 Sol" }],
    defaultModel: "",
    effortLevels: ["low", "medium", "high", "xhigh"],
    defaultEffort: "medium",
    permissionModes: [{ id: "auto", label: "Auto", description: "…" }],
    defaultPermissionMode: "",
  },
};

const SHELL: SplitAgentChoice = {
  name: "shell",
  displayName: "Plain Terminal",
  installed: true,
  kind: "shell",
};

const HARNESS: SplitAgentChoice = {
  name: "deepseek-harness",
  displayName: "DeepSeek Harness",
  installed: true,
  kind: "cli",
  acceptsPrompts: false,
};

const MISSING: SplitAgentChoice = {
  name: "kimi",
  displayName: "Kimi Code",
  installed: false,
  kind: "cli",
};

function store(agents: SplitAgentChoice[], open = vi.fn(async () => undefined)) {
  return {
    hook: createNewPaneChatStore({ folder: "C:/work/app", agents, open }),
    open,
  };
}

describe("the Agentic IDE's new chat", () => {
  it("offers the coding CLIs this machine has, not the chat catalog's providers", () => {
    const { hook } = store([CLAUDE, CODEX, MISSING]);
    expect(hook.getState().providerOptions().map((p) => p.id)).toEqual([
      "claude",
      "codex",
      "kimi",
    ]);
  });

  it("leaves out anything there would be nothing to say to", () => {
    // A shell is a prompt, not an agent; the Harness reads its own chat in a
    // browser and never sees what is typed into its pane.
    expect(chatStartableAgents([CLAUDE, SHELL, HARNESS]).map((a) => a.name)).toEqual(["claude"]);
  });

  it("keeps a CLI that is not installed, disabled, rather than hiding it", () => {
    const { hook } = store([CLAUDE, MISSING]);
    const kimi = hook.getState().providerById("kimi");
    expect(kimi).not.toBeNull();
    expect(kimi?.connected).toBe(false);
    expect(kimi?.cli_installed).toBe(false);
  });

  it("draws each row with its own CLI mark, never a letter in a box", () => {
    const { hook } = store([CLAUDE, MISSING]);
    expect(hook.getState().providerOptions().map((p) => p.agentMark)).toEqual([
      "claude",
      "kimi",
    ]);
  });

  it("opens on the first installed CLI and its defaults", () => {
    const { hook } = store([MISSING, CLAUDE]);
    expect(firstStartableAgent([MISSING, CLAUDE])?.name).toBe("claude");
    expect(hook.getState().draft).toMatchObject({
      provider: "claude",
      effort: "high",
      model: "",
      permissionMode: "",
      cwd: "C:/work/app",
    });
  });

  it("re-seats the other picks when the CLI changes", async () => {
    // A model, a ladder step and a stance belong to ONE CLI's vocabulary.
    // Carrying Claude's "high" onto Codex would leave a pick Codex may not
    // take, and the model would be one it would reject outright.
    const { hook } = store([CLAUDE, CODEX]);
    await hook.getState().setDraft({ model: "claude-opus-5" });
    expect(hook.getState().draft.model).toBe("claude-opus-5");
    await hook.getState().setDraft({ provider: "codex" });
    expect(hook.getState().draft).toMatchObject({
      provider: "codex",
      model: "",
      effort: "medium",
    });
  });

  it("starts nothing until the first message", async () => {
    const { hook, open } = store([CLAUDE, CODEX]);
    await hook.getState().setDraft({ provider: "codex", model: "gpt-5.6-sol" });
    await hook.getState().setDraft({ effort: "xhigh" });
    expect(open).not.toHaveBeenCalled();
  });

  it("opens the pane ON the picks, with the message", async () => {
    const open = vi.fn(async (_: NewPaneRequest) => undefined);
    const { hook } = store([CLAUDE, CODEX], open);
    await hook.getState().setDraft({ provider: "codex" });
    await hook.getState().setDraft({ model: "gpt-5.6-sol", effort: "xhigh" });
    await hook.getState().setDraft({ permissionMode: "auto" });
    await hook.getState().send("refactor the parser", []);
    expect(open).toHaveBeenCalledWith({
      agent: "codex",
      model: "gpt-5.6-sol",
      effort: "xhigh",
      permissionMode: "auto",
      text: "refactor the parser",
      attachments: [],
    });
  });

  it("says so instead of opening nothing when no CLI is picked", async () => {
    const open = vi.fn(async () => undefined);
    const { hook } = store([], open);
    await hook.getState().send("hello", []);
    expect(open).not.toHaveBeenCalled();
    expect(hook.getState().lastError).toBeTruthy();
  });

  it("keeps the draft when the pane could not be opened", async () => {
    // The sentence is the person's; a failed spawn must not swallow it.
    const open = vi.fn(async () => {
      throw new Error("Claude Code is not on PATH.");
    });
    const { hook } = store([CLAUDE], open);
    await hook.getState().send("hello", []);
    expect(hook.getState().lastError).toBe("Claude Code is not on PATH.");
    expect(hook.getState().draft.provider).toBe("claude");
    expect(hook.getState().busy).toBe(false);
  });

  it("switches Plan on and back to the stance it came from", async () => {
    const { hook } = store([CLAUDE]);
    await hook.getState().setDraft({ permissionMode: "default", buildMode: "default" });
    await hook.getState().setPlan(true);
    expect(hook.getState().draft.permissionMode).toBe("plan");
    await hook.getState().setPlan(false);
    expect(hook.getState().draft.permissionMode).toBe("default");
  });

  it("seeds a CLI with no picks as 'whatever it defaults to'", () => {
    expect(seedDraft(MISSING, "C:/work/app")).toMatchObject({
      provider: "kimi",
      model: "",
      effort: "",
      permissionMode: "",
    });
  });
});
