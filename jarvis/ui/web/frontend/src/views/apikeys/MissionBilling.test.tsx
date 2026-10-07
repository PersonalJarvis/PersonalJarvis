/**
 * The paid-fallback switch bills the user once it is on, so the cases that
 * matter are the ones where a wrong pixel means money: it starts off, it asks
 * and explains before anything is saved, a cancel saves nothing, it never
 * shows on unless the server holds it, and it cannot be turned on where it
 * would do nothing or where no priced key exists.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { loadLocaleChunk, useI18nStore } from "@/i18n";
import { MISSION_BILLING_ANCHOR, clearApiKeysTabRequest, requestApiKeysTab } from "@/lib/apiKeysTab";
import type { MissionBilling as Billing } from "@/types/missions";
import { MissionBilling } from "./MissionBilling";

const BASE: Billing = {
  paid_api_fallback: false,
  subscription_mode: true,
  per_mission_cap_usd: 2,
  daily_cap_usd: 10,
  spent_last_24h_usd: 0,
  paid_provider: { provider: "claude-api", model: "claude-sonnet-4-6", price_known: true },
};

const providerName = (provider: string) =>
  provider === "claude-api" ? { label: "Anthropic", logoId: "claude-api" } : null;

let server: Billing | null;
let failPut: string | null;
let puts: unknown[];

beforeAll(async () => {
  useI18nStore.getState().setUi("en", { push: false });
  await loadLocaleChunk("providers");
});

beforeEach(() => {
  server = { ...BASE };
  failPut = null;
  puts = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url !== "/api/mission-billing") return { ok: false, status: 404, json: async () => ({}) } as Response;
      if (server === null) return { ok: false, status: 404, json: async () => ({ detail: "Not Found" }) } as Response;
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { paid_api_fallback: boolean };
        puts.push(body);
        if (failPut) return { ok: false, status: 500, json: async () => ({ detail: failPut }) } as Response;
        server = { ...server, paid_api_fallback: body.paid_api_fallback };
      }
      const snapshot = { ...server };
      return { ok: true, status: 200, json: async () => snapshot } as Response;
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearApiKeysTabRequest();
});

function renderBilling() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MissionBilling providerName={providerName} />
    </QueryClientProvider>,
  );
}

const theSwitch = () => screen.getByRole("switch", { name: "Continue on API keys when subscriptions run out", hidden: true });

async function loaded() {
  await screen.findByText("Off · missions wait until a subscription has room again");
}

describe("MissionBilling", () => {
  it("loads the setting and starts off", async () => {
    renderBilling();
    await loaded();
    expect(theSwitch().getAttribute("aria-checked")).toBe("false");
    expect((theSwitch() as HTMLButtonElement).disabled).toBe(false);
    expect(puts).toEqual([]);
  });

  it("explains the costs before anything is saved", async () => {
    renderBilling();
    await loaded();

    fireEvent.click(theSwitch());
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Continue on your API key?");
    expect(dialog.textContent).toContain(
      "When every connected subscription is used up, missions keep running on your own API key instead of waiting.",
    );
    expect(dialog.textContent).toContain("Anthropic bills that key for every request:");
    expect(screen.getByTestId("mission-billing-provider").textContent).toContain("claude-sonnet-4-6");
    expect(dialog.textContent).toContain("Each mission stops at $2, and all automatic use stops at $10 per day.");
    expect(dialog.textContent).toContain("You can switch this off at any time.");
    // Nothing is saved and the switch does not pretend to be on.
    expect(puts).toEqual([]);
    expect(theSwitch().getAttribute("aria-checked")).toBe("false");
    // The safe choice holds the focus.
    expect(document.activeElement?.textContent).toBe("Cancel");
  });

  it("saves only after the confirmation and then shows on", async () => {
    renderBilling();
    await loaded();

    fireEvent.click(theSwitch());
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(theSwitch().getAttribute("aria-checked")).toBe("true"));
    expect(puts).toEqual([{ paid_api_fallback: true }]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("On · Anthropic · at most $2 per mission, $10 a day")).toBeTruthy();
  });

  it("saves nothing when the dialog is cancelled", async () => {
    renderBilling();
    await loaded();

    fireEvent.click(theSwitch());
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(puts).toEqual([]);
    expect(theSwitch().getAttribute("aria-checked")).toBe("false");
  });

  it("stays off and shows the error when turning on fails", async () => {
    failPut = "Config is locked";
    renderBilling();
    await loaded();

    fireEvent.click(theSwitch());
    fireEvent.click(await screen.findByRole("button", { name: "Turn on" }));
    expect(await screen.findByText("Not saved: Config is locked")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(theSwitch().getAttribute("aria-checked")).toBe("false");
  });

  it("turns off at once, without a dialog", async () => {
    server = { ...BASE, paid_api_fallback: true };
    renderBilling();
    await screen.findByText("On · Anthropic · at most $2 per mission, $10 a day");

    fireEvent.click(theSwitch());
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(theSwitch().getAttribute("aria-checked")).toBe("false");
    await waitFor(() => expect(puts).toEqual([{ paid_api_fallback: false }]));
    await loaded();
  });

  it("springs back on and shows the error when turning off fails", async () => {
    server = { ...BASE, paid_api_fallback: true };
    failPut = "Disk full";
    renderBilling();
    await screen.findByText("On · Anthropic · at most $2 per mission, $10 a day");

    fireEvent.click(theSwitch());
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toBe("Not saved: Disk full");
    expect(theSwitch().getAttribute("aria-checked")).toBe("true");
  });

  it("shows what was spent in the last 24 hours", async () => {
    server = { ...BASE, paid_api_fallback: true, spent_last_24h_usd: 3.456 };
    renderBilling();
    expect(
      await screen.findByText("On · Anthropic · at most $2 per mission, $10 a day · $3.46 spent in the last 24 hours"),
    ).toBeTruthy();
  });

  it("cannot be turned on where missions already run on keys", async () => {
    server = { ...BASE, subscription_mode: false };
    renderBilling();
    await screen.findByText("Missions already run on your API keys");
    expect((theSwitch() as HTMLButtonElement).disabled).toBe(true);
  });

  it("cannot be turned on without a key that has a known price", async () => {
    server = { ...BASE, paid_provider: null };
    renderBilling();
    await screen.findByText("Needs an API key with a known price");
    expect((theSwitch() as HTMLButtonElement).disabled).toBe(true);
  });

  it("can always be turned off, even without a priced key", async () => {
    server = { ...BASE, paid_api_fallback: true, paid_provider: null };
    renderBilling();
    await screen.findByText("On · no API key with a known price yet");
    expect((theSwitch() as HTMLButtonElement).disabled).toBe(false);
  });

  it("leaves the section out on a backend without the setting", async () => {
    server = null;
    const { container } = renderBilling();
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("brings the setting into view when a link asked for it", async () => {
    requestApiKeysTab("agents", MISSION_BILLING_ANCHOR);
    renderBilling();
    await loaded();
    await waitFor(() => expect(document.activeElement).toBe(theSwitch()));
  });
});
