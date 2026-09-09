import { expect, test } from "vitest";
import type { ProviderOption } from "@/store/agentChat";
import type { SocietyProviderRow } from "@/lib/societyApi";
import { isFreeOpenCodeModel, modelEffort, modelSeats } from "./modelChoices";

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
