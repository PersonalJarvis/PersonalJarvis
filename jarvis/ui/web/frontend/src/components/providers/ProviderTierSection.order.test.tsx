/**
 * Card ORDER inside one provider tier.
 *
 * Two rules pull against each other and both matter:
 *
 * 1. The provider the tier actually runs on has to LEAD, or a fresh pick
 *    (onboarding's local path is the sharpest case: the user chooses "run
 *    local" and then has to find that card) sits buried in catalog order
 *    below cards they never touched.
 * 2. Nothing may reorder while the list is on screen — a card that jumps to
 *    the top the instant it is activated moves the next card under the
 *    pointer mid-click.
 *
 * No jest-dom in this repo — assertions use plain values.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { TierSection } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor } from "@/hooks/useProviders";

function brainCard(over: Partial<ProviderDescriptor> = {}): ProviderDescriptor {
  return {
    id: "cloud-a",
    label: "Cloud A",
    tier: "brain",
    auth_mode: "api_key",
    secret_keys: ["cloud_a_api_key"],
    secrets_set: { cloud_a_api_key: true },
    dashboard_url: null,
    login_cli: null,
    install_hint: null,
    credential_path_hint: null,
    configured: true,
    active: false,
    cli_installed: null,
    credential_help: null,
    signup_url: null,
    billing: "api",
    alt_credential: null,
    ...over,
  };
}

/** Card labels in rendered order. */
function order(): string[] {
  return screen
    .getAllByRole("listitem")
    .map((li) => li.textContent ?? "")
    .map((text) => text.match(/Cloud A|Cloud B|Local X/)?.[0] ?? "");
}

beforeEach(() => {
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(
    async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({}),
        text: async () => "{}",
      }) as Response,
  ) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("leads with the provider the tier runs on, then configured, then the rest", () => {
  render(
    <TierSection
      providers={[
        brainCard(),
        brainCard({ id: "cloud-b", label: "Cloud B", configured: false, secrets_set: {} }),
        // Keyless local provider — active without any credential, which is
        // exactly the shape the onboarding local path leaves behind.
        brainCard({
          id: "local-x",
          label: "Local X",
          auth_mode: "none",
          secret_keys: [],
          secrets_set: {},
          configured: false,
          active: true,
          billing: "local",
        }),
      ]}
      onChanged={() => {}}
      onActivateOptimistic={() => {}}
    />,
  );

  expect(order()).toEqual(["Local X", "Cloud A", "Cloud B"]);
});

it("does not re-sort under the pointer when the active provider changes", () => {
  const view = render(
    <TierSection
      providers={[
        brainCard(),
        brainCard({ id: "cloud-b", label: "Cloud B", configured: false, secrets_set: {} }),
        brainCard({ id: "local-x", label: "Local X", configured: true, active: true }),
      ]}
      onChanged={() => {}}
      onActivateOptimistic={() => {}}
    />,
  );
  expect(order()).toEqual(["Local X", "Cloud A", "Cloud B"]);

  // The user activates a different card: the lead is anchored to the mount, so
  // the list stays put. The new pick leads on the next visit to this tier.
  view.rerender(
    <TierSection
      providers={[
        brainCard({ active: true }),
        brainCard({ id: "cloud-b", label: "Cloud B", configured: false, secrets_set: {} }),
        brainCard({ id: "local-x", label: "Local X", configured: true, active: false }),
      ]}
      onChanged={() => {}}
      onActivateOptimistic={() => {}}
    />,
  );
  expect(order()).toEqual(["Local X", "Cloud A", "Cloud B"]);
});
