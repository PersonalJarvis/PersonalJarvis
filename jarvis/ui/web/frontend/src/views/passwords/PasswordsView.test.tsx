/**
 * The properties pinned here are the ones the section's trustworthiness rests
 * on, not its looks:
 *
 * * the neutral right pane EXPLAINS the section — where a password goes, what
 *   the AI can and cannot see — so a first-time user reads the disclosure
 *   before anything else exists on the screen;
 * * opening the editor shows the same disclosure again, above the inputs, so
 *   nobody types a password without having been told the assistant gains
 *   access to it;
 * * the list is grouped by whose account an entry is once the assistant holds
 *   accounts of its own — and stays a plain list while everything is the
 *   user's;
 * * a password is fetched ONLY by the explicit reveal click. Rendering and
 *   selecting must not call the reveal endpoint.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

// The real ViewHeader lives in ChatsView, which drags the chat stack into the
// test for the sake of one <header>. A faithful stub keeps the surface.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({
    title,
    subtitle,
    right,
  }: {
    title: string;
    subtitle?: string;
    right?: React.ReactNode;
  }) => (
    <header>
      <h1>{title}</h1>
      {subtitle && <p>{subtitle}</p>}
      {right}
    </header>
  ),
}));

const listLogins = vi.fn();
const revealLogin = vi.fn();

vi.mock("./api", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("./api");
  return {
    ...actual,
    listLogins: (...args: unknown[]) => listLogins(...args),
    revealLogin: (...args: unknown[]) => revealLogin(...args),
    createLogin: vi.fn(),
    updateLogin: vi.fn(),
    deleteLogin: vi.fn(),
  };
});

import { PasswordsView } from "./PasswordsView";
import type { LoginSummary } from "./api";

function entry(overrides: Partial<LoginSummary> = {}): LoginSummary {
  return {
    service_id: "github",
    label: "GitHub",
    domains: ["github.com"],
    username: "octocat",
    notes: "",
    has_password: true,
    has_totp: false,
    status: "unknown",
    created_at: null,
    updated_at: null,
    last_used_at: null,
    owner: "user",
    kind: "website",
    fields: {},
    secret_names: [],
    ...overrides,
  };
}

beforeEach(() => {
  listLogins.mockReset().mockResolvedValue([]);
  revealLogin.mockReset();
});

afterEach(cleanup);

describe("PasswordsView trust disclosure", () => {
  it("explains the section before any password exists", async () => {
    render(<PasswordsView />);
    await waitFor(() => expect(listLogins).toHaveBeenCalled());

    expect(screen.getByText("passwords.trust.title")).toBeTruthy();
    expect(screen.getByText("passwords.trust.ai_body")).toBeTruthy();
    expect(screen.getByText("passwords.trust.storage_body")).toBeTruthy();
  });

  it("repeats the disclosure inside the editor, above the inputs", async () => {
    render(<PasswordsView />);
    await waitFor(() => expect(listLogins).toHaveBeenCalled());

    fireEvent.click(screen.getAllByText("passwords.add")[0]);

    const disclosure = screen.getByText("passwords.editor_disclosure");
    const passwordLabel = screen.getByText("passwords.password");
    expect(disclosure).toBeTruthy();
    // DOM order is the reading order: the warning must come before the field.
    expect(
      disclosure.compareDocumentPosition(passwordLabel) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("PasswordsView owner grouping", () => {
  it("stays a plain list while every account is the user's", async () => {
    listLogins.mockResolvedValue([entry(), entry({ service_id: "bank", label: "Bank" })]);
    render(<PasswordsView />);
    await waitFor(() => expect(screen.getByText("GitHub")).toBeTruthy());

    expect(screen.queryByText("passwords.owner_group_user")).toBeNull();
    expect(screen.queryByText("passwords.owner_group_agent")).toBeNull();
  });

  it("labels both groups once the assistant holds an account of its own", async () => {
    listLogins.mockResolvedValue([
      entry(),
      entry({ service_id: "forge", label: "Forge", owner: "agent" }),
    ]);
    render(<PasswordsView />);
    await waitFor(() => expect(screen.getByText("Forge")).toBeTruthy());

    expect(screen.getByText("passwords.owner_group_user")).toBeTruthy();
    expect(screen.getByText("passwords.owner_group_agent")).toBeTruthy();
  });
});

describe("PasswordsView reveal semantics", () => {
  it("fetches a password only for the explicit reveal click", async () => {
    listLogins.mockResolvedValue([entry()]);
    revealLogin.mockResolvedValue({
      service_id: "github",
      username: "octocat",
      password: "hunter2-long",
      totp_secret: null,
    });
    render(<PasswordsView />);
    await waitFor(() => expect(screen.getByText("GitHub")).toBeTruthy());

    fireEvent.click(screen.getByText("GitHub"));
    await waitFor(() =>
      expect(screen.getByLabelText("passwords.reveal")).toBeTruthy(),
    );
    // Selecting an entry must not touch the reveal endpoint.
    expect(revealLogin).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText("passwords.reveal"));
    await waitFor(() => expect(screen.getByText("hunter2-long")).toBeTruthy());
    expect(revealLogin).toHaveBeenCalledTimes(1);
  });
});
