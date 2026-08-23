/**
 * The row CLICK gesture inside a provider tier list.
 *
 * One click on a row selects it: the body opens AND the provider becomes the
 * active one (maintainer request 2026-08-23 — "one click is enough; the row
 * that is open is the active one"). The Use control keeps switching without
 * touching the body. A row that would only warn ("save a key first") must
 * NOT fire a switch on the click that was there to open its key field.
 *
 * No jest-dom in this repo — assertions use plain values.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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

let fetchMock: ReturnType<typeof vi.fn>;

/** The brain switches fired so far, as provider ids in order. */
function switches(): string[] {
  return fetchMock.mock.calls
    .filter(
      (c) =>
        String(c[0]).includes("/api/brain/switch") &&
        (c[1] as RequestInit | undefined)?.method === "POST",
    )
    .map((c) => JSON.parse((c[1] as RequestInit).body as string).provider as string);
}

function renderTier(providers: ProviderDescriptor[]) {
  const onActivateOptimistic = vi.fn();
  render(
    <TierSection
      providers={providers}
      onChanged={() => {}}
      onActivateOptimistic={onActivateOptimistic}
    />,
  );
  return { onActivateOptimistic };
}

beforeEach(() => {
  fetchMock = vi.fn(
    async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({}),
        text: async () => "{}",
      }) as Response,
  );
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("one click on a configured row opens it AND makes it the active provider", async () => {
  const { onActivateOptimistic } = renderTier([
    brainCard({ active: true }),
    brainCard({ id: "cloud-b", label: "Cloud B" }),
  ]);
  // The list opens on the active provider; the other row is collapsed.
  expect(screen.queryByTestId("provider-body-cloud-b")).toBeNull();

  fireEvent.click(screen.getByTestId("provider-row-cloud-b"));

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  expect(onActivateOptimistic).toHaveBeenCalledWith("brain", "cloud-b");
  await waitFor(() => expect(switches()).toEqual(["cloud-b"]));
});

it("a double click switches once — the second click never closes the row again", async () => {
  renderTier([brainCard({ active: true }), brainCard({ id: "cloud-b", label: "Cloud B" })]);
  const row = screen.getByTestId("provider-row-cloud-b");

  // A real double click: click, click(detail 2), dblclick.
  fireEvent.click(row);
  fireEvent.click(row, { detail: 2 });
  fireEvent.doubleClick(row);

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  await waitFor(() => expect(switches()).toEqual(["cloud-b"]));
});

it("a click on a row WITHOUT a key only opens its key field — no switch, no warning", () => {
  const { onActivateOptimistic } = renderTier([
    brainCard({ active: true }),
    brainCard({ id: "cloud-b", label: "Cloud B", configured: false, secrets_set: {} }),
  ]);

  fireEvent.click(screen.getByTestId("provider-row-cloud-b"));

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  expect(switches()).toEqual([]);
});

it("a click on the open active row closes it and switches nothing", () => {
  const { onActivateOptimistic } = renderTier([
    brainCard({ active: true }),
    brainCard({ id: "cloud-b", label: "Cloud B" }),
  ]);
  expect(screen.getByTestId("provider-body-cloud-a")).toBeTruthy();

  fireEvent.click(screen.getByTestId("provider-row-cloud-a"));

  expect(screen.queryByTestId("provider-body-cloud-a")).toBeNull();
  expect(onActivateOptimistic).not.toHaveBeenCalled();
  expect(switches()).toEqual([]);
});

it("Enter on a focused row selects it like a click", async () => {
  renderTier([brainCard({ active: true }), brainCard({ id: "cloud-b", label: "Cloud B" })]);
  const row = screen.getByTestId("provider-row-cloud-b");

  fireEvent.keyDown(row, { key: "Enter" });

  expect(screen.getByTestId("provider-body-cloud-b")).toBeTruthy();
  await waitFor(() => expect(switches()).toEqual(["cloud-b"]));
});
