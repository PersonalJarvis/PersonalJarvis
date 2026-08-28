/**
 * The composer's live provider state.
 *
 * A saved key is not a working one. On the maintainer's own box on
 * 2026-08-26 four of nine CONNECTED rows were unusable — an Anthropic key
 * revoked (401), OpenAI and OpenRouter out of credit (429/402), Gemini not
 * answering inside twenty seconds — and the picker offered all nine as
 * equals. The chat then answered "I can't reach my provider" and the person
 * had no way to see which seat to move to.
 *
 * So the picker carries the live sweep: a dot on the row and, when it is
 * broken, the reason in plain words. These pin that the reason vocabulary is
 * translated rather than echoed from a log, and that a row stays exactly as
 * it was when nothing is known about it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  AgentComposer,
  healthReasonLabel,
  isApiKeyHealthReason,
} from "@/components/agentchat/AgentComposer";
import { AgentChatStoreProvider } from "@/components/agentchat/AgentChatStoreContext";
import { EMPTY_TIMELINE } from "@/components/agentchat/reduce";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import type { AgentChatCatalog, ProviderHealth } from "@/lib/agentChatApi";

/** The real dictionary, so a missing key shows up here rather than in the UI. */
import de from "@/i18n/locales/de.json";
import en from "@/i18n/locales/en.json";
import es from "@/i18n/locales/es.json";

function lookup(dict: Record<string, unknown>) {
  return (key: string): string => {
    const value = key.split(".").reduce<unknown>(
      (node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined),
      dict,
    );
    if (typeof value !== "string") throw new Error(`missing translation: ${key}`);
    return value;
  };
}

describe("provider health reasons", () => {
  it("says what is wrong in words, never the backend's token", () => {
    const t = lookup(en);
    expect(healthReasonLabel("bad_key", t)).toBe("Key rejected");
    expect(healthReasonLabel("no_credits", t)).toBe("Out of credit");
    expect(healthReasonLabel("rate_limited", t)).toBe("Out of credit");
    expect(healthReasonLabel("unreachable", t)).toBe("Unreachable");
    expect(healthReasonLabel("timeout", t)).toBe("Too slow");
  });

  it("falls back to a general sentence for a reason it has not met", () => {
    const t = lookup(en);
    // A future provider_test status must never leak a log word into the picker.
    expect(healthReasonLabel("some_new_status", t)).toBe("Not answering");
    expect(healthReasonLabel("", t)).toBe("Not answering");
  });

  it("treats only a rejected key as an API-key reason", () => {
    expect(isApiKeyHealthReason("bad_key")).toBe(true);
    expect(isApiKeyHealthReason("no_credits")).toBe(false);
    expect(isApiKeyHealthReason("ok")).toBe(false);
  });

  it("is translated in every shipped locale", () => {
    for (const dict of [de, en, es]) {
      const t = lookup(dict as Record<string, unknown>);
      for (const reason of ["bad_key", "no_credits", "unreachable", "timeout", "nonsense"]) {
        expect(healthReasonLabel(reason, t).length).toBeGreaterThan(0);
      }
      expect(t("agent_chat.provider_health_ok").length).toBeGreaterThan(0);
    }
  });

  it("keeps the three locales in step on every health key", () => {
    const keys = Object.keys((de as Record<string, Record<string, unknown>>).agent_chat).filter((k) =>
      k.startsWith("provider_health_"),
    );
    expect(keys.length).toBeGreaterThan(0);
    for (const dict of [en, es]) {
      const other = (dict as Record<string, Record<string, unknown>>).agent_chat;
      for (const key of keys) expect(typeof other[key]).toBe("string");
    }
  });
});


// ── the dot on the row ────────────────────────────────────────────────────

function row(id: string, label: string, runner: "brain" | "claude-cli" = "brain") {
  return {
    id,
    label,
    family: id,
    runner,
    models_source: runner === "claude-cli" ? ("curated" as const) : ("live" as const),
    curated_models: [],
    default_model: "",
    keyless: false,
    native_resume: false,
    effort_levels: ["low", "high"],
    default_effort: "high",
    permission_modes: [{ id: "ask", label: "Ask before acting", description: "" }],
    default_permission_mode: "ask",
    cli_installed: runner === "claude-cli" ? true : null,
  };
}

const CATALOG: AgentChatCatalog = {
  default_cwd: "C:\work",
  shell: "pwsh",
  providers: [row("claude-api", "Anthropic Claude"), row("grok", "xAI Grok")],
};

function seed(health: Record<string, ProviderHealth>) {
  useEventStore.setState({ connected: true, wsWarming: false, assistantName: "Jarvis" });
  useAgentChatStore.setState({
    catalog: CATALOG,
    connections: [
      { jarvis: "claude-api", key_set: true, api_key_set: true, is_active_brain: true },
      { jarvis: "grok", key_set: true, api_key_set: true, is_active_brain: false },
    ],
    catalogError: null,
    backendOutdated: false,
    liveModels: {},
    health,
    sessions: [],
    activeSessionId: null,
    activeSession: null,
    timeline: EMPTY_TIMELINE,
    draft: {
      provider: "claude-api",
      model: "",
      effort: "high",
      permissionMode: "ask",
      buildMode: "ask",
      cwd: "C:\work",
    },
    busy: false,
    lastError: null,
    loadCatalog: async () => {},
    loadSessions: async () => {},
    loadModels: async () => {},
    loadHealth: async () => {},
  } as never);
}

describe("the health dot in the provider picker", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("marks a rejected key red and a working seat green, with the reason in words", async () => {
    seed({
      "claude-api": {
        provider: "claude-api",
        status: "error",
        reason: "bad_key",
        detail: "Claude (API-Key): 401 Unauthorized",
      },
      grok: { provider: "grok", status: "ok", reason: "ok", detail: "xAI Grok: ok" },
    });
    render(
      <AgentChatStoreProvider store={useAgentChatStore}>
        <AgentComposer />
      </AgentChatStoreProvider>,
    );
    // The picked row's dot rides the closed pill too, so the state of the seat
    // you are about to type on is visible without opening anything.
    const onPill = screen.getByTestId("provider-health-claude-api");
    expect(onPill.getAttribute("data-health")).toBe("error");

    fireEvent.click(screen.getByTestId("composer-provider"));
    await waitFor(() => expect(screen.getByTestId("provider-health-grok")).toBeTruthy());

    const broken = screen.getAllByTestId("provider-health-claude-api");
    expect(broken).toHaveLength(2); // the pill and the row
    for (const dot of broken) {
      expect(dot.getAttribute("data-health")).toBe("error");
      expect(dot.className).toContain("bg-destructive");
      // The provider's own words are the tooltip; the label is plain language.
      expect(dot.getAttribute("title")).toContain("401");
      expect(dot.getAttribute("aria-label")).toBe("Key rejected");
    }

    const working = screen.getByTestId("provider-health-grok");
    expect(working.getAttribute("data-health")).toBe("ok");
    expect(working.className).toContain("bg-muted-foreground");
  });

  it("draws no dot at all before the sweep lands", async () => {
    seed({});
    render(
      <AgentChatStoreProvider store={useAgentChatStore}>
        <AgentComposer />
      </AgentChatStoreProvider>,
    );
    fireEvent.click(screen.getByTestId("composer-provider"));
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    expect(screen.queryByTestId("provider-health-claude-api")).toBeNull();
    expect(screen.queryByTestId("provider-health-grok")).toBeNull();
  });

  it("keeps a broken row selectable — a rate limit passes, a refused click does not", async () => {
    seed({
      "claude-api": {
        provider: "claude-api",
        status: "error",
        reason: "no_credits",
        detail: "402",
      },
    });
    render(
      <AgentChatStoreProvider store={useAgentChatStore}>
        <AgentComposer />
      </AgentChatStoreProvider>,
    );
    fireEvent.click(screen.getByTestId("composer-provider"));
    const option = await screen.findByRole("option", { name: /Anthropic Claude/ });
    expect(option.getAttribute("aria-disabled")).not.toBe("true");
  });

  it("does not paint Key rejected on a coding CLI even if the sweep leaked a bad key", async () => {
    // The dual Claude row keeps id `claude-api` when Claude Code answers. A
    // backend that still probed the Anthropic key would send bad_key; the
    // picker must not show that on a CLI seat.
    const catalog: AgentChatCatalog = {
      default_cwd: "C:\\work",
      shell: "pwsh",
      providers: [row("claude-api", "Anthropic Claude", "claude-cli"), row("grok", "xAI Grok")],
    };
    useEventStore.setState({ connected: true, wsWarming: false, assistantName: "Jarvis" });
    useAgentChatStore.setState({
      catalog,
      connections: [
        {
          jarvis: "claude-api",
          key_set: true,
          api_key_set: false,
          oauth_connected: true,
          is_active_brain: false,
        },
        { jarvis: "grok", key_set: true, api_key_set: true, is_active_brain: false },
      ],
      catalogError: null,
      backendOutdated: false,
      liveModels: {},
      health: {
        "claude-api": {
          provider: "claude-api",
          status: "error",
          reason: "bad_key",
          detail: "Claude (API-Key): 401 Unauthorized",
        },
      },
      sessions: [],
      activeSessionId: null,
      activeSession: null,
      timeline: EMPTY_TIMELINE,
      draft: {
        provider: "claude-api",
        model: "",
        effort: "high",
        permissionMode: "ask",
        buildMode: "ask",
        cwd: "C:\\work",
      },
      busy: false,
      lastError: null,
      loadCatalog: async () => {},
      loadSessions: async () => {},
      loadModels: async () => {},
      loadHealth: async () => {},
    } as never);
    render(
      <AgentChatStoreProvider store={useAgentChatStore}>
        <AgentComposer />
      </AgentChatStoreProvider>,
    );
    expect(screen.queryByTestId("provider-health-claude-api")).toBeNull();
    expect(screen.queryByText("Key rejected")).toBeNull();
    fireEvent.click(screen.getByTestId("composer-provider"));
    await waitFor(() => expect(screen.getByRole("listbox")).toBeTruthy());
    expect(screen.queryByText("Key rejected")).toBeNull();
    expect(screen.queryByTestId("provider-health-claude-api")).toBeNull();
  });
});
