import { expect, test } from "vitest";
import type { ProviderOption } from "@/store/agentChat";
import type { SocietyProviderRow } from "@/lib/societyApi";
import { isFreeOpenCodeModel, modelEffort, modelSeats, visibleModels } from "./modelChoices";

const option = (overrides: Partial<ProviderOption> = {}): ProviderOption => ({
  id: "cli", label: "CLI", family: "cli", runner: "grok-cli", connected: false,
  cli_installed: true, keyless: false, active: false, models_source: "curated", native_resume: true,
  curated_models: [{ id: "small", label: "Small", efforts: ["low"] }], default_model: "small",
  effort_levels: ["low", "high"], default_effort: "high", permission_modes: [], default_permission_mode: "ask",
  ...overrides,
});
const accounts = [{ id: "cli", subscription: true, accounts: [
  { id: "signed-in", label: "Work", connected: true }, { id: "expired", label: "Old", connected: false },
] }] as SocietyProviderRow[];

test("a signed-in subscription seat wins over a stale summary credential flag", () => {
  const seats = modelSeats([option()], accounts, {});
  expect(seats).toHaveLength(1);
  expect(seats[0].accounts.map((account) => account.id)).toEqual(["signed-in"]);
});

test("a subscription account cannot authorize an API provider or a missing CLI", () => {
  expect(modelSeats([option({ cli_installed: false })], accounts, {})).toEqual([]);
  expect(modelSeats([option({ runner: "brain", cli_installed: null })], accounts, {})).toEqual([]);
});

test("live labels retain model-specific effort metadata and duplicate ids collapse", () => {
  const [seat] = modelSeats([option({ connected: true })], [], { cli: [
    { id: "small", label: "Small live" }, { id: "small", label: "Small live" },
  ] });
  expect(seat.provider.curated_models).toHaveLength(1);
  expect(modelEffort(seat, "small", "high")).toBe("low");
});

test("free model detection does not infer price from labels, size or unrelated aliases", () => {
  for (const id of ["opencode/next-free", "openrouter/vendor/model:free", "opencode/big-pickle"]) {
    expect(isFreeOpenCodeModel({ id, label: "Model" })).toBe(true);
  }
  for (const id of ["opencode/gpt-5-nano", "other/big-pickle", "opencode/freedom", "opencode/free-preview-paid"]) {
    expect(isFreeOpenCodeModel({ id, label: "Free model" })).toBe(false);
  }
});

test.each(["claude-cli", "codex-cli", "cursor-cli", "opencode-cli", "kimi-cli", "glm-cli", "grok-cli", "agy-cli", "dsh-cli"] as const)(
  "%s stays selectable when its own model catalog is unavailable", (runner) => {
    const [seat] = modelSeats([option({ runner, connected: true, curated_models: [], default_model: "" })], [], {}, "Use default");
    expect(seat.provider.curated_models).toEqual([{ id: "", label: "Use default" }]);
    expect(visibleModels(seat, seat.provider.curated_models, false, "")).toEqual(seat.provider.curated_models);
  },
);

test("the default model never authorizes a disconnected, missing, or API seat", () => {
  expect(modelSeats([option({ curated_models: [] })], [], {})).toEqual([]);
  expect(modelSeats([option({ cli_installed: false, curated_models: [] })], accounts, {})).toEqual([]);
  const [seat] = modelSeats([option({ runner: "api", cli_installed: null, connected: true, curated_models: [] })], [], {});
  expect(seat.provider.curated_models).toEqual([]);
});
