import { describe, expect, it } from "vitest";

import type { SocietyAccount, SocietyProviderRow } from "@/lib/societyApi";
import type { ProviderOption } from "@/store/agentChat";

import { accountChoice, brainSeats, defaultSeat, effortsFor } from "./brainPicker";

function option(over: Partial<ProviderOption>): ProviderOption {
  return {
    id: "openai",
    label: "OpenAI",
    family: "openai",
    runner: "brain",
    models_source: "live",
    curated_models: [],
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
  };
}

function account(id: string, connected = true, email: string | null = null): SocietyAccount {
  return { id, label: id, connected, mode: "subscription", message: "", email, tier: null, warning: null };
}

function societyRow(over: Partial<SocietyProviderRow>): SocietyProviderRow {
  return {
    id: "openai",
    label: "OpenAI",
    family: "openai",
    runner: "brain",
    subscription: false,
    keyless: false,
    platform: null,
    accounts: [],
    ...over,
  };
}

describe("brainSeats", () => {
  it("lists only connected rows, subscriptions first, then keys, then local", () => {
    const seats = brainSeats(
      [
        option({ id: "ollama", label: "Ollama", runner: "api", keyless: true }),
        option({ id: "gemini", label: "Google Gemini", connected: false }),
        option({ id: "openai", label: "OpenAI" }),
        option({ id: "claude-api", label: "Anthropic Claude", runner: "claude-cli", cli_installed: true }),
      ],
      [
        societyRow({ id: "claude-api", runner: "claude-cli", subscription: true, platform: "claude" }),
        societyRow({ id: "ollama", runner: "brain", keyless: true }),
      ],
    );
    expect(seats.map((s) => `${s.kind}:${s.provider.id}`)).toEqual([
      "subscription:claude-api",
      "api:openai",
      "local:ollama",
    ]);
  });

  it("decides the kind from the runner when the society route is missing", () => {
    const seats = brainSeats(
      [option({ id: "openai-codex", label: "Codex", runner: "codex-cli", cli_installed: true })],
      [],
    );
    expect(seats[0].kind).toBe("subscription");
  });

  it("keeps only signed-in logins and offers a choice only when there are two", () => {
    const rows = [
      societyRow({
        id: "claude-api",
        subscription: true,
        platform: "claude",
        accounts: [account("a", true, "a@x.de"), account("b", false), account("c", true)],
      }),
    ];
    const [seat] = brainSeats([option({ id: "claude-api", runner: "claude-cli", cli_installed: true })], rows);
    expect(seat.accounts.map((a) => a.id)).toEqual(["a", "c"]);
    expect(accountChoice(seat).length).toBe(2);
    const [single] = brainSeats(
      [option({ id: "claude-api", runner: "claude-cli", cli_installed: true })],
      [{ ...rows[0], accounts: [account("a")] }],
    );
    expect(accountChoice(single)).toEqual([]);
  });

  it("starts on the active brain, else the first seat", () => {
    const seats = brainSeats(
      [option({ id: "openai" }), option({ id: "grok", label: "xAI Grok", active: true })],
      [],
    );
    expect(defaultSeat(seats)?.provider.id).toBe("grok");
    expect(defaultSeat(brainSeats([option({ id: "openai" })], []))?.provider.id).toBe("openai");
    expect(defaultSeat([])).toBeNull();
  });

  it("narrows the effort ladder to the picked model's own", () => {
    const [seat] = brainSeats(
      [
        option({
          id: "agy",
          runner: "agy-cli",
          cli_installed: true,
          effort_levels: ["low", "medium", "high"],
          curated_models: [
            { id: "pro", label: "Pro", efforts: ["low", "high"] },
            { id: "flash", label: "Flash" },
          ],
        }),
      ],
      [],
    );
    expect(effortsFor(seat, "pro")).toEqual(["low", "high"]);
    expect(effortsFor(seat, "flash")).toEqual(["low", "medium", "high"]);
    expect(effortsFor(null, "pro")).toEqual([]);
  });
});
