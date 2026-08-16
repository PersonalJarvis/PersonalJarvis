import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommunityTab, type CommunityResponse } from "@/views/PluginsCommunity";

// What the reader must be able to see BEFORE installing: the actual text a
// skill would hand the assistant, and the actual picture a wallpaper installs.
const SKILL_TEXT = "---\nname: three-point-check\n---\n\nAlways answer in three bullets.\n";

const COMMUNITY: CommunityResponse = {
  status: "fresh",
  plugins: [],
  skills: [
    {
      name: "three-point-check",
      title: "Three Point Check",
      description: "Summarize any topic in three bullets",
      publisher: "octocat",
      version: "1.0.0",
      categories: ["productivity"],
      source_url: "https://github.com/PersonalJarvis/marketplace",
      raw_url: "https://raw.example/skills/three-point-check/SKILL.md",
      installed: false,
    },
  ],
  wallpapers: [
    {
      name: "rain-antenna-city",
      title: "Rain Antenna City",
      description: "Neon rooftops in the rain",
      publisher: "octocat",
      version: "1.0.0",
      categories: ["dark"],
      source_url: "https://github.com/PersonalJarvis/marketplace",
      raw_url: "https://raw.example/wallpapers/rain-antenna-city.webp",
      theme: "dark",
      installed: false,
    },
  ],
};

function installFetchMock() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/marketplace/community" && method === "GET") {
      return { ok: true, status: 200, json: async () => COMMUNITY } as Response;
    }
    if (url === "/api/marketplace/community/three-point-check/contents") {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          kind: "skill",
          name: "three-point-check",
          title: "Three Point Check",
          root: "skills/three-point-check",
          files: [
            { path: "SKILL.md", size: 72, text: SKILL_TEXT, truncated: false },
          ],
          image_url: null,
          error: null,
        }),
      } as Response;
    }
    if (url === "/api/marketplace/community/install/rain-antenna-city") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, kind: "wallpaper", id: "rain-antenna-city" }),
      } as Response;
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CommunityTab />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("reading an entry before installing it", () => {
  it("shows the published SKILL.md text when a skill card is opened", async () => {
    installFetchMock();
    renderTab();

    // Opening happens by clicking the card itself — reading must not require
    // intending to install.
    fireEvent.click(await screen.findByTitle(/Read what Three Point Check/i));

    expect(await screen.findByText(/Always answer in three bullets/)).toBeDefined();
    expect(screen.getByText("skills/three-point-check")).toBeDefined();
    expect(screen.getByText("SKILL.md")).toBeDefined();
  });

  it("says so honestly when the published file cannot be read", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/marketplace/community") {
        return { ok: true, status: 200, json: async () => COMMUNITY } as Response;
      }
      return { ok: false, status: 502, json: async () => ({}) } as Response;
    });
    (globalThis as unknown as { fetch: typeof fetch }).fetch =
      fetchMock as unknown as typeof fetch;
    renderTab();

    fireEvent.click(await screen.findByTitle(/Read what Three Point Check/i));
    expect(
      await screen.findByText(/could not be read just now/i),
    ).toBeDefined();
  });

  it("lists wallpapers and installs the previewed one by name", async () => {
    const fetchMock = installFetchMock();
    renderTab();

    fireEvent.click(await screen.findByTitle(/Preview Rain Antenna City/i));
    // The picture at full size IS the disclosure for a wallpaper.
    const preview = await screen.findByAltText("Rain Antenna City");
    expect(preview.getAttribute("src")).toBe(
      "https://raw.example/wallpapers/rain-antenna-city.webp",
    );

    // Scoped to the dialog: the skill card behind it carries an Install too.
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Install" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([u]) => String(u) === "/api/marketplace/community/install/rain-antenna-city",
        ),
      ).toBe(true),
    );
  });
});
