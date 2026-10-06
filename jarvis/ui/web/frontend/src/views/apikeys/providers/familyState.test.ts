import { describe, expect, it } from "vitest";
import type { ProviderDescriptor, SectionHealth } from "@/hooks/useProviders";
import type { AgentStatus, ProviderFamily } from "@/lib/providerFamilies";
import { familyState, familyStatusLine, type FamilyContext } from "./familyState";

const t = (key: string) => key;

function family(overrides: Partial<ProviderFamily> = {}): ProviderFamily {
  return {
    id: "openai",
    label: "OpenAI",
    logo_id: "openai",
    local: false,
    key_slot: "openai_api_key",
    key_present: false,
    separate_keys: [],
    dashboard_url: null,
    signup_url: null,
    subscription: { kind: "codex", provider_id: "codex", label: "OpenAI Codex" },
    provider_ids: ["openai", "codex", "openai-live", "openai-api"],
    hidden_ids: ["openai-live-subscription"],
    agent_ids: ["openai", "openai-codex"],
    ...overrides,
  };
}

function card(id: string, tier: ProviderDescriptor["tier"], extra: Partial<ProviderDescriptor> = {}) {
  return { id, label: id, tier, active: false, configured: true, ...extra } as ProviderDescriptor;
}

function ctx(overrides: Partial<FamilyContext> = {}): FamilyContext {
  return {
    providers: [card("openai-live", "realtime"), card("openai-api", "stt"), card("gemini-live", "realtime")],
    subscriptions: {},
    agents: null,
    health: {},
    ...overrides,
  };
}

const agents = (active: string): AgentStatus => ({
  brain_primary: active,
  mapping: [
    { jarvis: "openai-codex", key_set: true, is_active_brain: active === "openai-codex", billing: "subscription_or_api" },
    { jarvis: "gemini", key_set: true, is_active_brain: active === "gemini", billing: "api" },
  ],
});

describe("familyState", () => {
  it("takes only the company's own cards", () => {
    const state = familyState(family(), ctx());
    expect(state.members.map((m) => m.id)).toEqual(["openai-live", "openai-api"]);
  });

  it("reports a subscription login as connected, but not a CLI running on a key", () => {
    const signedIn = familyState(
      family(),
      ctx({ subscriptions: { codex: { installed: true, connected: true, mode: "chatgpt", message: "", user_email: "a@b.c" } } }),
    );
    expect(signedIn.subscriptionOn).toBe(true);
    expect(signedIn.account).toBe("a@b.c");
    expect(signedIn.connected).toBe(true);

    const onKey = familyState(
      family(),
      ctx({ subscriptions: { codex: { installed: true, connected: true, mode: "api_key", message: "" } } }),
    );
    expect(onKey.subscriptionOn).toBe(false);
    expect(onKey.connected).toBe(false);
  });

  it("lists the jobs it powers and the jobs it could take over", () => {
    const state = familyState(
      family({ key_present: true }),
      ctx({
        providers: [card("openai-live", "realtime", { active: true }), card("openai-api", "stt")],
        agents: agents("openai-codex"),
      }),
    );
    expect(state.uses).toEqual(["voice", "agents"]);
    expect(state.canDo).toEqual(["voice", "agents", "hearing"]);
  });

  it("puts a failure on the company whose member is failing, matching hidden cards too", () => {
    const health: Record<string, SectionHealth> = {
      realtime: { status: "error", reason: "quota", detail: "GPT-Live: out of credit", subject_id: "openai-live-subscription" },
    };
    const state = familyState(
      family({ key_present: true }),
      ctx({ providers: [card("openai-live", "realtime", { active: true })], health }),
    );
    expect(state.failing).toBe("GPT-Live: out of credit");
  });

  it("never blames a company for another company's failure", () => {
    const health: Record<string, SectionHealth> = {
      realtime: { status: "error", reason: "quota", detail: "Gemini Live: out of credit", subject_id: "gemini-live" },
    };
    const state = familyState(
      family({ key_present: true }),
      ctx({ providers: [card("openai-live", "realtime", { active: true })], health }),
    );
    expect(state.failing).toBeNull();
  });

  it("counts a Cloud-project sign-in as connected without a key", () => {
    const vertex = family({
      id: "vertex",
      subscription: null,
      provider_ids: ["vertex"],
      agent_ids: ["vertex"],
      hidden_ids: [],
    });
    const state = familyState(
      vertex,
      ctx({ providers: [card("vertex", "brain", { credential_note: "Signed in through the project." })] }),
    );
    expect(state.viaProject).toBe(true);
    expect(state.connected).toBe(true);
    expect(familyStatusLine(vertex, state, t)).toBe("providers_page.status_project");
  });
});

describe("familyStatusLine", () => {
  it("names exactly what is connected", () => {
    const f = family({ key_present: true });
    const both = familyState(
      f,
      ctx({ subscriptions: { codex: { installed: true, connected: true, mode: "chatgpt", message: "" } } }),
    );
    expect(familyStatusLine(f, both, t)).toBe("providers_page.status_both");
    expect(familyStatusLine(family(), familyState(family(), ctx()), t)).toBe("providers_page.status_none");
    const local = family({ id: "local", local: true, key_slot: null, subscription: null });
    expect(familyStatusLine(local, familyState(local, ctx()), t)).toBe("providers_page.status_local");
  });
});
