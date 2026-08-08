/**
 * Local Mode on the Subagents tab.
 *
 * This tab was the one place the switch did not reach, which made the whole
 * feature look unreliable: a keyless install turned Local Mode on, saw the
 * provider tiers filter correctly, then opened the tab that picks the WORKER —
 * the one decision a local-only install cares most about — and got the full
 * wall of hosted accounts back. These tests pin that it filters here too, and
 * that it filters the subscription-login cards as well, not only the API column.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

import { setLocalMode } from "@/lib/localMode";
import { JarvisAgentSection } from "./JarvisAgentSection";

function row(
  jarvis: string,
  billing: string,
  {
    active = false,
    keyless = false,
    label = `${jarvis} card`,
  }: { active?: boolean; keyless?: boolean; label?: string } = {},
) {
  return {
    jarvis,
    worker_slug: jarvis,
    env_var: `${jarvis.toUpperCase()}_API_KEY`,
    env_fallback: null,
    key_set: keyless,
    api_key_set: false,
    dedicated_key_set: false,
    shared_key_set: false,
    oauth_connected: false,
    credential_source: "none",
    secret_key: keyless ? null : `jarvis_agent_${jarvis}_api_key`,
    dashboard_url: null,
    credential_help: null,
    is_active_brain: active,
    keyless,
    // A distinct label per row: the raw slug also appears in card body copy, so
    // querying by it would match more than the card title.
    label,
    billing,
  };
}

const STATUS = {
  configured: true,
  enabled: true,
  binary_path: "openclaw",
  binary_detected: null,
  version_pin: null,
  time_cap_min: null,
  concurrency: null,
  state_dir_root: null,
  brain_primary: "ollama",
  provider_slug: "ollama",
  model_override: null,
  sub_model_override: null,
  model_resolved: null,
  mapping: [
    row("openai", "api"),
    row("gemini", "api"),
    row("claude-api", "subscription_or_api"),
    row("openai-codex", "subscription_or_api"),
    row("ollama", "local", { keyless: true }),
    row("local-openai", "local", { keyless: true }),
  ],
};

function mockFetch(status: unknown = STATUS) {
  return vi.fn().mockImplementation(async (url: string) => {
    const u = String(url);
    if (u.includes("/api/jarvis-agent/status")) {
      return { ok: true, json: async () => status };
    }
    return { ok: true, json: async () => ({}) };
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  setLocalMode(false);
  window.localStorage.clear();
});

describe("JarvisAgentSection — Local Mode", () => {
  it("shows every worker while Local Mode is off", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<JarvisAgentSection />);

    expect((await screen.findAllByText("openai card")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("ollama card").length).toBeGreaterThan(0);
    expect(screen.queryByTestId("local-mode-notice")).toBeNull();
  });

  it("keeps only own-hardware workers when Local Mode is on", async () => {
    setLocalMode(true);
    vi.stubGlobal("fetch", mockFetch());
    render(<JarvisAgentSection />);

    expect((await screen.findAllByText("ollama card")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("local-openai card").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("openai card")).toHaveLength(0);
    expect(screen.queryAllByText("gemini card")).toHaveLength(0);
  });

  it("filters the subscription-login cards too, not just the API column", async () => {
    setLocalMode(true);
    vi.stubGlobal("fetch", mockFetch());
    render(<JarvisAgentSection />);

    await screen.findAllByText("ollama card");
    // The three dual-billed / subscription connection cards render their own
    // headings; none of them survives a filter that means "own hardware only".
    expect(screen.queryByText("Subscription logins")).toBeNull();
    expect(screen.queryByText("API keys")).toBeNull();
  });

  it("explains the shorter list and counts what it hid", async () => {
    setLocalMode(true);
    vi.stubGlobal("fetch", mockFetch());
    render(<JarvisAgentSection />);

    const notice = await screen.findByTestId("local-mode-notice");
    // openai, gemini, claude-api, openai-codex — four hosted workers.
    expect(notice.getAttribute("data-hidden-count")).toBe("4");
  });

  it("never hides the worker that is actually active", async () => {
    setLocalMode(true);
    const status = {
      ...STATUS,
      mapping: [
        row("openai", "api", { active: true }),
        row("gemini", "api"),
        row("ollama", "local", { keyless: true }),
      ],
    };
    vi.stubGlobal("fetch", mockFetch(status));
    render(<JarvisAgentSection />);

    await waitFor(() => expect(screen.getByText("ollama card")).toBeTruthy());
    expect(screen.getAllByText("openai card").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("gemini card")).toHaveLength(0);
  });
});
