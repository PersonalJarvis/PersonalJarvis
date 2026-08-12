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
  favorite: false,
  organization: null,
  tags: ["uni"],
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
  favorite: true,
  organization: null,
  tags: [],
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
  favorite: false,
  birthday: null,
  organization: "ACME GmbH",
  role: null,
  urls: [],
  tags: ["uni"],
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

    // Two "Add contact" buttons exist on an empty book: the header one and
    // the empty-state CTA. Either opens the same create dialog.
    fireEvent.click((await screen.findAllByRole("button", { name: /add contact/i }))[0]);
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

  it("filters by relationship chips (toggle on/off)", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);
    await screen.findByText("Christoph Meyer");

    fireEvent.click(screen.getByRole("button", { name: /partner · 1/i }));
    expect(screen.queryByText("Christoph Meyer")).toBeNull();
    expect(screen.getByText("Laura")).toBeTruthy();

    // Clicking the active chip again clears the filter.
    fireEvent.click(screen.getByRole("button", { name: /partner · 1/i }));
    expect(screen.getByText("Christoph Meyer")).toBeTruthy();
  });

  it("groups the list by first letter", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);
    await screen.findByText("Christoph Meyer");
    // "C" appears only as Christoph's group header; "L" is both Laura's group
    // header and her avatar initial.
    expect(screen.getByText("C")).toBeTruthy();
    expect(screen.getAllByText("L").length).toBeGreaterThan(0);
  });

  it("reloads the list when a contact changes elsewhere (voice/CLI)", async () => {
    let listing: unknown[] = [CHRISTOPH_SUMMARY];
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: listing } }),
    });
    render(<ContactsView />);
    await screen.findByText("Christoph Meyer");
    expect(screen.queryByText("Laura")).toBeNull();

    listing = [CHRISTOPH_SUMMARY, LAURA_SUMMARY];
    fireEvent(
      window,
      new CustomEvent("jarvis:contact-changed", {
        detail: { action: "created", slug: "laura", name: "Laura" },
      }),
    );
    expect(await screen.findByText("Laura")).toBeTruthy();
  });

  it("preselects a contact from the ?contact= deep link", async () => {
    window.history.replaceState({}, "", "/?view=contacts&contact=christoph_meyer");
    installFetchMock({
      "GET /api/contacts/christoph_meyer": () => ({ body: CHRISTOPH_FULL }),
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);
    expect(
      await screen.findByRole("link", { name: /christoph@example\.com/i }),
    ).toBeTruthy();
    window.history.replaceState({}, "", "/");
  });

  it("pins favorites in their own group", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);
    await screen.findByText("Laura");
    expect(screen.getByText("Favorites")).toBeTruthy();
  });

  it("filters by tag chips", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY, LAURA_SUMMARY] } }),
    });
    render(<ContactsView />);
    await screen.findByText("Christoph Meyer");

    fireEvent.click(screen.getByRole("button", { name: /#uni · 1/i }));
    expect(screen.getByText("Christoph Meyer")).toBeTruthy();
    expect(screen.queryByText("Laura")).toBeNull();
  });

  it("offers vCard import and export in the header", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [CHRISTOPH_SUMMARY] } }),
    });
    render(<ContactsView />);
    await screen.findByText("Christoph Meyer");
    expect(screen.getByRole("button", { name: /import vcard/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /export vcard/i })).toBeTruthy();
  });

  it("asks before discarding unsaved dialog changes", async () => {
    installFetchMock({
      "GET /api/contacts": () => ({ body: { contacts: [] } }),
    });
    render(<ContactsView />);
    fireEvent.click((await screen.findAllByRole("button", { name: /add contact/i }))[0]);
    const nameInput = await screen.findByPlaceholderText("Christoph Meyer");
    fireEvent.change(nameInput, { target: { value: "Anna" } });

    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    expect(await screen.findByText(/discard changes\?/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));
    expect(screen.getByPlaceholderText("Christoph Meyer")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /^discard$/i }));
    expect(screen.queryByPlaceholderText("Christoph Meyer")).toBeNull();
  });
});
