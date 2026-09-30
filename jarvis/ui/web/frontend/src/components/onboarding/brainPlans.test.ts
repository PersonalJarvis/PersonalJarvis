import { describe, expect, it } from "vitest";
import type { ProviderDescriptor } from "@/hooks/useProviders";
import type { StarterPlan } from "@/hooks/useStarterPlans";
import { planKeysComplete, startableProviders } from "./brainPlans";

function provider(over: Partial<ProviderDescriptor> & { id: string }): ProviderDescriptor {
  return {
    label: over.id,
    tier: "brain",
    auth_mode: "api_key",
    secret_keys: [`${over.id}_api_key`],
    secrets_set: {},
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: false,
    active: false,
    ...over,
  } as ProviderDescriptor;
}

const plan: StarterPlan = {
  id: "openai-live",
  label: "OpenAI GPT-Live",
  summary: "",
  mode: "realtime",
  recommended: true,
  assignments: {},
  key_slots: [{ family: "openai", slot: "openai_api_key", label: "OpenAI", present: false }],
  keys_complete: false,
  ready_sections: [],
};

describe("startableProviders", () => {
  it("keeps only brain providers that take a pasted key", () => {
    const list = startableProviders([
      provider({ id: "openai" }),
      provider({ id: "tts-only", tier: "tts" as ProviderDescriptor["tier"] }),
      provider({ id: "oauth", auth_mode: "oauth" as ProviderDescriptor["auth_mode"] }),
      provider({ id: "fixed", brain_switchable: false }),
      provider({ id: "keyless", secret_keys: [] }),
    ]);
    expect(list.map((p) => p.id)).toEqual(["openai"]);
  });

  it("puts recommended first, then ones that already have a key", () => {
    const list = startableProviders([
      provider({ id: "a" }),
      provider({ id: "b", secrets_set: { b_api_key: true } }),
      provider({ id: "c", recommended: true }),
    ]);
    expect(list.map((p) => p.id)).toEqual(["c", "b", "a"]);
  });
});

describe("plans", () => {
  const cards = [provider({ id: "gemini" }), provider({ id: "openai" })];

  it("is complete once the plan's key reaches its card, own or shared", () => {
    expect(planKeysComplete(plan, cards)).toBe(false);
    const shared = [provider({ id: "openai", secrets_effective: { openai_api_key: true } })];
    expect(planKeysComplete(plan, shared)).toBe(true);
  });

  it("is never complete for a plan without key slots", () => {
    expect(planKeysComplete({ ...plan, key_slots: [] }, cards)).toBe(false);
  });
});
