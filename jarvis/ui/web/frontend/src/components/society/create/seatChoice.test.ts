import { describe, expect, it } from "vitest";

import type { CuratedModel } from "@/lib/agentChatApi";
import type { ProviderOption } from "@/store/agentChat";

import { runtimeSeats } from "../chat/modelChoices";
import type { BrainSeat } from "./brainPicker";
import {
  accessModels,
  API_KEY_ACCOUNT,
  defaultModel,
  providerChoices,
  SUBSCRIPTION_ACCOUNT,
} from "./seatChoice";

function models(...ids: string[]): CuratedModel[] {
  return ids.map((id) => ({ id, label: id }));
}

function seat(over: Partial<ProviderOption>, kind: BrainSeat["kind"] = "api"): BrainSeat {
  return {
    kind,
    accounts: [],
    provider: {
      id: "openai",
      label: "OpenAI",
      family: "openai",
      runner: "brain",
      models_source: "live",
      curated_models: models("gpt-5.5"),
      default_model: "",
      keyless: false,
      native_resume: false,
      effort_levels: [],
      default_effort: "",
      permission_modes: [],
      default_permission_mode: "",
      cli_installed: null,
      connected: true,
      active: false,
      ...over,
    },
  };
}

const claude = seat({
  id: "claude-api", label: "Anthropic Claude", family: "claude", runner: "claude-cli", cli_installed: true,
  curated_models: models("opusplan", "claude-opus-5", "claude-sonnet-5"),
}, "subscription");
const codex = seat({ id: "openai-codex", label: "OpenAI Codex", family: "openai", runner: "codex-cli", cli_installed: true }, "subscription");
const openai = seat({});
const ollama = seat({ id: "ollama", label: "Ollama", family: "ollama", keyless: true, curated_models: models("qwen3:8b") }, "local");

describe("providerChoices", () => {
  it("respects an explicit empty access list and a missing required CLI", () => {
    expect(providerChoices([claude], { "claude-api": [] })).toEqual([]);
    const missing = { ...claude, provider: { ...claude.provider, cli_installed: false } };
    expect(providerChoices([missing], { "claude-api": ["subscription"] })).toEqual([]);
  });

  it.each([
    ["grok-build", "grok", "xai", "xai", "grok-cli"],
    ["antigravity", "gemini", "antigravity", "gemini", "agy-cli"],
  ])("groups %s with its API brand while retaining each access's own models", (subscription, api, family, brand, runner) => {
    const [choice] = providerChoices([
      seat({ id: api, family: brand, curated_models: models("api-model-1") }),
      seat({ id: subscription, family, runner, cli_installed: true, curated_models: models("subscription-model-2") }, "subscription"),
    ]);
    expect(choice.id).toBe(brand);
    expect(choice.options.map((option) => [option.kind, option.seat.provider.id, accessModels(option)[0].id])).toEqual([
      ["subscription", subscription, "subscription-model-2"], ["api", api, "api-model-1"],
    ]);
  });

  it("folds a brand's subscription and API rows into one provider", () => {
    const [choice] = providerChoices([codex, openai]);
    expect(choice.label).toBe("OpenAI");
    expect(choice.options.map((o) => [o.kind, o.seat.provider.id, o.accountId])).toEqual([
      ["subscription", "openai-codex", ""],
      ["api", "openai", ""],
    ]);
  });

  it("offers Claude both ways on Jarvis when both work, each with its own account value", () => {
    const [choice] = providerChoices([claude], { "claude-api": ["api", "subscription"] });
    const [sub, api] = choice.options;
    expect([sub.kind, sub.accountId, sub.extraUsage]).toEqual(["subscription", SUBSCRIPTION_ACCOUNT, false]);
    // The subscription is Claude Code with its own aliases; the key gets real model ids only.
    expect(accessModels(sub).map((m) => m.id)).toContain("opusplan");
    expect([api.kind, api.accountId]).toEqual(["api", API_KEY_ACCOUNT]);
    expect(accessModels(api).map((m) => m.id)).not.toContain("opusplan");
  });

  it("never offers a subscription on Jarvis' loop without the vendor CLI", () => {
    const missing = { ...claude, provider: { ...claude.provider, cli_installed: false } };
    const [choice] = providerChoices([missing], { "claude-api": ["api", "subscription"] });
    expect(choice.options.map((o) => o.kind)).toEqual(["api"]);
  });

  it("marks Claude's login on Hermes / OpenClaw as extra usage", () => {
    const seats = runtimeSeats([claude, openai], ["claude-api", "openai"], [], ["claude-api"]);
    const [first] = providerChoices(seats, { "claude-api": ["api", "subscription"] }, true);
    expect(first.options.map((o) => [o.kind, o.accountId, o.extraUsage])).toEqual([
      ["subscription", SUBSCRIPTION_ACCOUNT, true],
      ["api", API_KEY_ACCOUNT, false],
    ]);
  });

  it("keeps a single seat as is when the backend names no ways (an older backend)", () => {
    const [choice] = providerChoices([claude]);
    expect(choice.options.map((o) => [o.kind, o.accountId])).toEqual([["subscription", ""]]);
  });

  it("lists providers with a subscription before key-only and local ones", () => {
    expect(providerChoices([ollama, openai, claude]).map((c) => c.id)).toEqual(["claude", "openai", "ollama"]);
  });
});

describe("defaultModel", () => {
  it("starts on the row's default when it lists it, else the newest", () => {
    const [choice] = providerChoices([seat({ default_model: "gpt-5.2", curated_models: models("gpt-5.2", "gpt-5.5") })]);
    expect(defaultModel(choice.options[0])).toBe("gpt-5.2");
    const [plain] = providerChoices([seat({ curated_models: models("gpt-5.2", "gpt-5.5") })]);
    expect(defaultModel(plain.options[0])).toBe("gpt-5.5");
    expect(defaultModel(null)).toBe("");
  });
});
