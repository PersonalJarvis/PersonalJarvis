/**
 * The Local Mode filter rules.
 *
 * Two properties are load-bearing and pinned here because breaking either one
 * turns a helpful filter into a confusing one:
 *
 *  - OFF is the identity function. The switch must be a pure addition; with it
 *    off, the provider console has to behave exactly as it did before it
 *    existed.
 *  - The ACTIVE provider is never hidden. A tier whose running provider is a
 *    hosted account still has to show that card, or the screen answers "what am
 *    I running on?" with an empty space.
 */
import { beforeEach, describe, expect, it } from "vitest";
import type { ProviderDescriptor } from "@/hooks/useProviders";
import {
  DEFAULT_LOCAL_MODE,
  LOCAL_MODE_KEY,
  filterForLocalMode,
  getLocalMode,
  parseLocalMode,
  readLocalMode,
  runsOnOwnHardware,
  setLocalMode,
  writeLocalMode,
} from "@/lib/localMode";

function card(
  id: string,
  billing: ProviderDescriptor["billing"],
  active = false,
): ProviderDescriptor {
  return {
    id,
    label: id,
    tier: "brain",
    auth_mode: billing === "local" ? "none" : "api_key",
    secret_keys: [],
    secrets_set: {},
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing,
    alt_credential: null,
  };
}

beforeEach(() => {
  window.localStorage.clear();
  setLocalMode(DEFAULT_LOCAL_MODE);
});

describe("localMode preference", () => {
  it("defaults to off so a first visit sees the full catalog", () => {
    expect(DEFAULT_LOCAL_MODE).toBe(false);
    expect(readLocalMode()).toBe(false);
  });

  it("round-trips through localStorage", () => {
    writeLocalMode(true);
    expect(window.localStorage.getItem(LOCAL_MODE_KEY)).toBe("1");
    expect(readLocalMode()).toBe(true);
    writeLocalMode(false);
    expect(readLocalMode()).toBe(false);
  });

  it("treats an unknown stored value as unset rather than as on", () => {
    expect(parseLocalMode("yes")).toBeNull();
    expect(parseLocalMode(null)).toBeNull();
    window.localStorage.setItem(LOCAL_MODE_KEY, "banana");
    expect(readLocalMode()).toBe(DEFAULT_LOCAL_MODE);
  });

  it("exposes the store imperatively for onboarding and tests", () => {
    setLocalMode(true);
    expect(getLocalMode()).toBe(true);
    setLocalMode(false);
    expect(getLocalMode()).toBe(false);
  });
});

describe("runsOnOwnHardware", () => {
  it("follows the backend billing field, never a provider id", () => {
    expect(runsOnOwnHardware(card("ollama", "local"))).toBe(true);
    // A hosted provider whose id merely *looks* local must not slip through —
    // this is the AP-21 trap the billing field exists to avoid.
    expect(runsOnOwnHardware(card("local-sounding-cloud", "api"))).toBe(false);
    expect(runsOnOwnHardware(card("codex", "subscription"))).toBe(false);
    expect(runsOnOwnHardware(card("gemini", "subscription_or_api"))).toBe(false);
  });
});

describe("filterForLocalMode", () => {
  const providers = [
    card("openai", "api"),
    card("ollama", "local"),
    card("codex", "subscription"),
    card("local-openai", "local"),
  ];

  it("is the identity function while it is off", () => {
    const result = filterForLocalMode(providers, false);
    expect(result.visible).toBe(providers);
    expect(result.hiddenCount).toBe(0);
    expect(result.keptActiveHosted).toBe(false);
  });

  it("keeps only own-hardware cards and counts what it hid", () => {
    const result = filterForLocalMode(providers, true);
    expect(result.visible.map((p) => p.id)).toEqual(["ollama", "local-openai"]);
    expect(result.hiddenCount).toBe(2);
    expect(result.keptActiveHosted).toBe(false);
  });

  it("never hides the provider the tier is actually running on", () => {
    const withActiveCloud = [card("openai", "api", true), card("ollama", "local")];
    const result = filterForLocalMode(withActiveCloud, true);
    expect(result.visible.map((p) => p.id)).toEqual(["openai", "ollama"]);
    expect(result.hiddenCount).toBe(0);
    expect(result.keptActiveHosted).toBe(true);
  });

  it("does not claim a kept hosted card when the active one is local anyway", () => {
    const result = filterForLocalMode(
      [card("openai", "api"), card("ollama", "local", true)],
      true,
    );
    expect(result.keptActiveHosted).toBe(false);
    expect(result.hiddenCount).toBe(1);
  });
});
