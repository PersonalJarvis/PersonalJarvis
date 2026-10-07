import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AntigravityStatus,
  Billing,
  ClaudeStatus,
  CodexStatus,
  GrokBuildStatus,
} from "@/hooks/useProviders";

/**
 * The provider catalog grouped by company — mirror of
 * `jarvis/ui/web/provider_families.py::build_families()`.
 *
 * The API Keys page shows each company once: its subscription login, the one
 * key that serves every feature, and the features it powers. Membership comes
 * from the backend (derived from the catalog), never from a list kept here.
 */
export type SubscriptionKind = "claude_cli" | "codex" | "antigravity" | "grok_build";

export interface ProviderFamily {
  id: string;
  label: string;
  /** The provider id whose brand mark stands for the company. */
  logo_id: string;
  /** The machine itself: no account, no key. */
  local: boolean;
  /** The one key slot every feature of the company reads first; null = keyless. */
  key_slot: string | null;
  key_present: boolean;
  /** Features that still hold their own different key instead of the main one. */
  separate_keys: { slot: string; surface: "agents" | "live_voice" | "codex" | "other" }[];
  dashboard_url: string | null;
  signup_url: string | null;
  subscription: { kind: SubscriptionKind; provider_id: string; label: string } | null;
  /** Member cards of `/api/providers`. */
  provider_ids: string[];
  /** Withdrawn cards that still run an existing selection (health names them). */
  hidden_ids: string[];
  /** Member rows of `/api/jarvis-agent/status`. */
  agent_ids: string[];
}

/** One worker row of `/api/jarvis-agent/status` (the fields this page reads). */
export interface AgentRowStatus {
  jarvis: string;
  label?: string | null;
  key_set: boolean;
  keyless?: boolean;
  is_active_brain: boolean;
  billing: Billing;
}

export interface AgentStatus {
  brain_primary: string;
  mapping: AgentRowStatus[];
}

export type SubscriptionStatus = CodexStatus | ClaudeStatus | AntigravityStatus | GrokBuildStatus;

const SUBSCRIPTION_STATUS_URL: Record<SubscriptionKind, string> = {
  codex: "/api/codex/status",
  claude_cli: "/api/claude/status",
  antigravity: "/api/antigravity/status",
  grok_build: "/api/grok-build/status",
};

export function subscriptionStatusUrl(kind: SubscriptionKind): string {
  return SUBSCRIPTION_STATUS_URL[kind];
}

/**
 * Whether a subscription login is what powers the CLI right now. Each CLI can
 * also run on an API key; that mode is the key section's business, so it does
 * not count as "subscription connected" here.
 */
export function subscriptionConnected(status: SubscriptionStatus | null | undefined): boolean {
  return Boolean(status?.connected) && status?.mode !== "api_key";
}

/** The signed-in account and plan, as one short line ("ruben@… · Claude Max"). */
export function subscriptionAccount(status: SubscriptionStatus | null | undefined): string | null {
  if (!status) return null;
  const email = status.user_email ?? null;
  const plan = "account_label" in status ? status.account_label ?? null : null;
  // Codex reports a generic login label, not a plan; only Claude names one.
  const showPlan = plan && "subscription_type" in status;
  return [email, showPlan ? plan : null].filter(Boolean).join(" · ") || null;
}

const REFRESH_EVENTS = [
  "jarvis:secret-configured",
  "jarvis:brain-switched",
  "jarvis:agent-switched",
  "jarvis:realtime-switched",
  "jarvis:provider-tested",
] as const;

/**
 * Everything the page needs besides `/api/providers`: the families, the four
 * subscription logins and the agent worker rows, loaded together and reloaded
 * on the same app events the provider list listens to.
 */
export function useProviderFamilies() {
  const [families, setFamilies] = useState<ProviderFamily[] | null>(null);
  const [subscriptions, setSubscriptions] = useState<
    Partial<Record<SubscriptionKind, SubscriptionStatus | null>>
  >({});
  const [agents, setAgents] = useState<AgentStatus | null>(null);
  const [error, setError] = useState<"restart_required" | "families_unavailable" | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const seq = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++seq.current;
    setLoading(true);
    const json = async <T,>(url: string): Promise<T | null> => {
      try {
        const res = await fetch(url, { cache: "no-store" });
        return res.ok ? ((await res.json()) as T) : null;
      } catch {
        // One unreachable status endpoint must not blank the page; its
        // section shows "unknown" instead.
        return null;
      }
    };
    // The family list alone reports WHY it is missing: a 404 means the running
    // backend predates the endpoint (the bundle updated without a restart).
    const familyRequest = async (): Promise<{ families: ProviderFamily[] } | "missing" | null> => {
      try {
        const res = await fetch("/api/providers/families", { cache: "no-store" });
        if (res.status === 404) return "missing";
        return res.ok ? ((await res.json()) as { families: ProviderFamily[] }) : null;
      } catch {
        return null;
      }
    };
    const kinds = Object.keys(SUBSCRIPTION_STATUS_URL) as SubscriptionKind[];
    const [familyData, agentData, ...statuses] = await Promise.all([
      familyRequest(),
      json<AgentStatus>("/api/jarvis-agent/status"),
      ...kinds.map((kind) => json<SubscriptionStatus>(SUBSCRIPTION_STATUS_URL[kind])),
    ]);
    if (mine !== seq.current) return;
    if (familyData && familyData !== "missing") {
      setFamilies(familyData.families);
      setError(null);
    } else {
      setError(familyData === "missing" ? "restart_required" : "families_unavailable");
    }
    setAgents(agentData);
    setSubscriptions(Object.fromEntries(kinds.map((kind, i) => [kind, statuses[i]])));
    setCheckedAt(Date.now());
    setLoading(false);
  }, []);

  useEffect(() => {
    void reload();
    let timer: number | undefined;
    // Several events land together after one save or switch; one reload.
    const onChange = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void reload(), 250);
    };
    for (const name of REFRESH_EVENTS) window.addEventListener(name, onChange);
    return () => {
      window.clearTimeout(timer);
      for (const name of REFRESH_EVENTS) window.removeEventListener(name, onChange);
    };
  }, [reload]);

  return { families, subscriptions, agents, error, checkedAt, loading, reload };
}

/**
 * Poll a subscription status endpoint after a login was started, until it
 * reports connected or the time runs out. A CLI login finishes in its own
 * browser or console window AFTER the request returned, so one refetch always
 * reads the old state.
 */
export async function pollUntilConnected(
  url: string,
  onTick: () => void | Promise<void>,
  { maxMs = 120_000, intervalMs = 2_500 }: { maxMs?: number; intervalMs?: number } = {},
): Promise<boolean> {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    await onTick();
    try {
      const res = await fetch(url, { cache: "no-store" });
      if (res.ok && subscriptionConnected((await res.json()) as SubscriptionStatus)) return true;
    } catch {
      // The app may be restarting; keep polling until the deadline.
    }
  }
  return false;
}
