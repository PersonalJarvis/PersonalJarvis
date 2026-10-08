import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { loadLocaleChunk, setUiLanguage } from "@/i18n";
import { browseToolRows, type ToolChoice } from "@/components/agentchat/toolChoices";
import { AddConnectorDialog, AddPluginTabs } from "@/views/AddConnectorDialog";

/**
 * Adding a connector is a name and an address. The dialog sends exactly
 * those (plus the advanced overrides when the owner set them), shows the
 * server's own reason when the backend refuses, and hands the created entry
 * to the caller so the connect step can start at once.
 */

const ADDED = {
  ok: true,
  plugin: { id: "acme", display_name: "Acme", source: "local" },
  detected: { auth: "oauth", transport: "http", server_name: "Acme MCP" },
};

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch(body: unknown, status = 200) {
  fetchMock = vi.fn(async () => respond(body, status));
  (globalThis as unknown as { fetch: typeof fetch }).fetch = fetchMock as unknown as typeof fetch;
}

function sentBody(): Record<string, unknown> {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(String(init.body));
}

beforeAll(async () => {
  setUiLanguage("en");
  await loadLocaleChunk("marketplace");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AddConnectorDialog", () => {
  it("needs only an address and sends name, address and automatic sign-in", async () => {
    stubFetch(ADDED);
    const onAdded = vi.fn();
    render(<AddConnectorDialog open onClose={() => {}} onAdded={onAdded} />);

    const continueButton = screen.getByTestId("connector-continue") as HTMLButtonElement;
    expect(continueButton.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Acme" } });
    fireEvent.change(screen.getByTestId("connector-url"), {
      target: { value: "https://mcp.acme.example/mcp" },
    });
    expect(continueButton.disabled).toBe(false);
    fireEvent.click(continueButton);

    await waitFor(() => expect(onAdded).toHaveBeenCalledWith(ADDED));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/marketplace/connectors");
    expect(sentBody()).toEqual({
      name: "Acme",
      url: "https://mcp.acme.example/mcp",
      auth: "auto",
      header_name: null,
    });
  });

  it("submits on Enter in the address field", async () => {
    stubFetch(ADDED);
    const onAdded = vi.fn();
    render(<AddConnectorDialog open onClose={() => {}} onAdded={onAdded} />);

    const url = screen.getByTestId("connector-url");
    fireEvent.change(url, { target: { value: "mcp.acme.example/mcp" } });
    fireEvent.keyDown(url, { key: "Enter" });

    await waitFor(() => expect(onAdded).toHaveBeenCalled());
  });

  it("sends the access-token override with its header name", async () => {
    stubFetch(ADDED);
    render(<AddConnectorDialog open onClose={() => {}} onAdded={() => {}} />);

    fireEvent.change(screen.getByTestId("connector-url"), {
      target: { value: "https://mcp.acme.example/mcp" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Advanced settings/ }));
    fireEvent.click(screen.getByRole("radio", { name: "Access token" }));
    fireEvent.change(screen.getByLabelText("Header name"), { target: { value: "X-API-Key" } });
    fireEvent.click(screen.getByTestId("connector-continue"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(sentBody()).toMatchObject({ auth: "token", header_name: "X-API-Key" });
  });

  it("shows the server's own reason when the connector is refused", async () => {
    stubFetch({ detail: "The server answered HTTP 500. Check that this is the address." }, 422);
    const onAdded = vi.fn();
    render(<AddConnectorDialog open onClose={() => {}} onAdded={onAdded} />);

    fireEvent.change(screen.getByTestId("connector-url"), {
      target: { value: "https://mcp.acme.example/mcp" },
    });
    fireEvent.click(screen.getByTestId("connector-continue"));

    expect((await screen.findByRole("alert")).textContent).toContain("HTTP 500");
    expect(onAdded).not.toHaveBeenCalled();
  });

  it("switches to the folder upload from the shared tabs", () => {
    const onChange = vi.fn();
    render(<AddPluginTabs mode="connector" onChange={onChange} />);

    expect(screen.getByTestId("add-plugin-tab-connector").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByTestId("add-plugin-tab-folder"));
    expect(onChange).toHaveBeenCalledWith("folder");
  });
});

describe("Add menu rows for plugins the owner added", () => {
  const row = (over: Partial<ToolChoice>): ToolChoice => ({
    id: "plugin:x",
    label: "X",
    description: "",
    category: "plugins",
    group: "X",
    brand: "x",
    available: false,
    tool_names: [],
    skill: "",
    ...over,
  });

  it("lists a connector the owner added before it is connected", () => {
    const added = row({ id: "plugin:acme", user_added: true });
    const shipped = row({ id: "plugin:notion" });

    expect(browseToolRows([added, shipped]).map((r) => r.id)).toEqual(["plugin:acme"]);
    expect(browseToolRows([added, shipped], "no").map((r) => r.id)).toEqual([
      "plugin:acme",
      "plugin:notion",
    ]);
  });
});
