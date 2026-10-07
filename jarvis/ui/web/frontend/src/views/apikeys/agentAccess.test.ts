import { describe, expect, it } from "vitest";

import type { AgentProviderPrefs } from "@/lib/agentProviderPrefs";
import type { AgentRowStatus, ProviderFamily } from "@/lib/providerFamilies";

import { accessPatch, familyAccess, nextDefault, onPatch, withPatch } from "./agentAccess";
import type { FamilyState } from "./providers/familyState";

const NONE: AgentProviderPrefs = { disabled: [], api_only: [], hidden_models: {} };

function family(over: Partial<ProviderFamily>): ProviderFamily {
  return {
    id: "openai", label: "OpenAI", logo_id: "openai", local: false, key_slot: "openai_api_key", key_present: true,
    separate_keys: [], dashboard_url: null, signup_url: null,
    subscription: { kind: "codex", provider_id: "openai-codex", label: "ChatGPT" },
    provider_ids: [], hidden_ids: [], agent_ids: ["openai", "openai-codex"],
    ...over,
  };
}

function state(subscriptionOn: boolean): FamilyState {
  return {
    members: [], subscription: null, subscriptionOn, account: null, keyOn: true, viaProject: false,
    connected: true, uses: [], canDo: [], failing: null,
  };
}

const ROWS: AgentRowStatus[] = [
  { jarvis: "openai", key_set: true, is_active_brain: false, billing: "api" },
  { jarvis: "openai-codex", key_set: true, is_active_brain: true, billing: "subscription_or_api" },
  { jarvis: "claude-api", key_set: true, is_active_brain: false, billing: "subscription_or_api" },
  { jarvis: "ollama", key_set: true, keyless: true, is_active_brain: false, billing: "local" },
  { jarvis: "local-openai", key_set: true, keyless: true, is_active_brain: false, billing: "local" },
];

const CLAUDE = family({
  id: "claude-api", label: "Anthropic", key_slot: "anthropic_api_key",
  subscription: { kind: "claude_cli", provider_id: "claude-cli", label: "Claude" }, agent_ids: ["claude-api", "claude-cli"],
});
const LOCAL = family({ id: "local", label: "This computer", local: true, key_slot: null, subscription: null, agent_ids: ["ollama", "local-openai"] });

describe("familyAccess", () => {
  it("uses a connected subscription first and is on by default", () => {
    const fa = familyAccess(family({}), ROWS, NONE, state(true));
    expect(fa.choices).toEqual(["subscription", "api_key"]);
    expect(fa.access).toBe("subscription");
    expect(fa.rows.map((r) => r.jarvis)).toEqual(["openai-codex"]);
    expect(fa.on).toBe(true);
  });

  it("falls back to the key while the subscription is signed out", () => {
    expect(familyAccess(family({}), ROWS, NONE, state(false)).access).toBe("api_key");
  });

  it("reads the picked access from which row is on", () => {
    const fa = familyAccess(family({}), ROWS, { ...NONE, disabled: ["openai-codex"] }, state(true));
    expect(fa.access).toBe("api_key");
    expect(fa.on).toBe(true);
  });

  it("reads Claude's single row from api_only", () => {
    expect(familyAccess(CLAUDE, ROWS, NONE, state(true)).access).toBe("subscription");
    const keyed = familyAccess(CLAUDE, ROWS, { ...NONE, api_only: ["claude-api"] }, state(true));
    expect(keyed.access).toBe("api_key");
    expect(keyed.dual).toBe(true);
  });

  it("is off when the chosen access is not ready", () => {
    const fa = familyAccess(family({ key_present: false }), ROWS, { ...NONE, disabled: ["openai-codex"] }, state(true));
    expect(fa.access).toBe("api_key");
    expect(fa.ready).toBe(false);
    expect(fa.on).toBe(false);
  });
});

describe("patches", () => {
  it("turning a company off disables every row; on enables only the chosen one", () => {
    const fa = familyAccess(family({}), ROWS, NONE, state(true));
    expect(onPatch(fa, false, NONE)).toEqual({ disabled: ["openai-codex", "openai"] });
    const off = { ...NONE, disabled: ["openai", "openai-codex", "grok"] };
    expect(onPatch(familyAccess(family({}), ROWS, off, state(true)), true, off)).toEqual({ disabled: ["grok", "openai"] });
  });

  it("switching access disables the other row, or flips Claude's api_only", () => {
    const fa = familyAccess(family({}), ROWS, NONE, state(true));
    expect(accessPatch(fa, "api_key", NONE)).toEqual({ disabled: ["openai-codex"] });
    const claude = familyAccess(CLAUDE, ROWS, NONE, state(true));
    expect(accessPatch(claude, "api_key", NONE)).toEqual({ api_only: ["claude-api"], disabled: [] });
  });

  it("this computer turns its local rows on and off together", () => {
    const fa = familyAccess(LOCAL, ROWS, NONE, undefined);
    expect(fa.access).toBe("local");
    expect(fa.rows.map((r) => r.jarvis)).toEqual(["ollama", "local-openai"]);
    expect(onPatch(fa, false, NONE)).toEqual({ disabled: ["ollama", "local-openai"] });
  });
});

describe("nextDefault", () => {
  it("keeps the default while its company is on", () => {
    const entries = [familyAccess(family({}), ROWS, NONE, state(true))];
    expect(nextDefault(entries, "openai-codex")).toBeNull();
  });

  it("moves the default to the same company's other access first", () => {
    const prefs = withPatch(NONE, { disabled: ["openai-codex"] });
    const entries = [familyAccess(CLAUDE, ROWS, prefs, state(true)), familyAccess(family({}), ROWS, prefs, state(true))];
    expect(nextDefault(entries, "openai-codex", "openai")?.jarvis).toBe("openai");
  });

  it("moves it to the first company still on when one is turned off", () => {
    const prefs = withPatch(NONE, { disabled: ["openai", "openai-codex"] });
    const entries = [familyAccess(family({}), ROWS, prefs, state(true)), familyAccess(CLAUDE, ROWS, prefs, state(true))];
    expect(nextDefault(entries, "openai-codex", "openai")?.jarvis).toBe("claude-api");
  });
});
