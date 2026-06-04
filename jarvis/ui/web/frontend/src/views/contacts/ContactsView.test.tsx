/**
 * Component tests for ContactsView (master–detail, user-managed CRUD).
 *
 * The view lists contacts from GET /api/contacts, loads the full record on
 * selection (GET /api/contacts/{slug}), and opens a create/edit dialog. These
 * tests drive it through a mocked fetch (mirroring SocialsView.test.tsx) and
 * force the UI language to English for deterministic labels.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ContactsView } from "@/views/contacts/ContactsView";
import { setUiLanguage } from "@/i18n";

interface RouteResult {
  status?: number;
  body: unknown;
}
interface Call {
  url: string;
  method: string;
}

function installFetchMock(routes: Record<string, () => RouteResult>) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, method });
    const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
    for (const key of keys) {
      const [routeMethod, prefix] = key.split(" ");
      if (method === routeMethod && url.startsWith(prefix)) {
        const { status = 200, body: resBody } = routes[key]();
        return {
          ok: status >= 200 && status < 300,
          status,
          statusText: status >= 200 && status < 300 ? "OK" : "ERR",
          json: async () => resBody,
          text: async () => JSON.stringify(resBody),
        } as Response;
      }
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
  return calls;
}

const CHRISTOPH_SUMMARY = {
  slug: "christoph_meyer",
  name: "Christoph Meyer",
  aliases: ["Chris"],
  relationship: "friend",
  primary_email: "christoph@example.com",
  primary_phone: "+4915123456789",
  email_count: 1,
  phone_count: 1,
};
const LAURA_SUMMARY = {
  slug: "laura",
  name: "Laura",
  aliases: [],
  relationship: "partner",
  primary_email: null,
  primary_phone: null,
  email_count: 0,
  phone_count: 0,
};
const CHRISTOPH_FULL = {
  slug: "christoph_meyer",
  name: "Christoph Meyer",
  aliases: ["Chris"],
  relationship: "friend",
  emails: ["christoph@example.com"],
  phones: ["+4915123456789"],
  address: { city: "Berlin" },
  note: "My oldest friend.",
  primary_email: "christoph@example.com",
  primary_phone: "+4915123456789",
  last_updated: null,
};

beforeEach(() => {
  setUiLanguage("en");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ContactsView (master–detail)", () => {
  it("lists contacts from the API", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);

    expect(await screen.findByText("Christoph Meyer")).toBeTruthy();
    expect(screen.getByText("Laura")).toBeTruthy();
  });

  it("selecting a contact loads and shows its details", async () => {
    installFetchMock({
      "GET /api/contacts/christoph_meyer": () => ({ body: CHRISTOPH_FULL }),
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /Christoph Meyer/i }));

    const mail = (await screen.findByRole("link", {
      name: /christoph@example\.com/i,
    })) as HTMLAnchorElement;
    expect(mail.href).toContain("mailto:christoph@example.com");
    // The README is shown in the detail pane.
    expect(screen.getByText(/My oldest friend\./i)).toBeTruthy();
  });

  it("the Add button opens the create dialog", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [] } }),
    });
    render(<ContactsView />);

    fireEvent.click(await screen.findByRole("button", { name: /add contact/i }));
    // The dialog has a name field (a create form, not a detail view).
    expect(await screen.findByPlaceholderText("Christoph Meyer")).toBeTruthy();
  });

  it("shows an empty state when there are no contacts", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [] } }),
    });
    render(<ContactsView />);
    expect(await screen.findByText(/no contacts yet/i)).toBeTruthy();
  });
});
