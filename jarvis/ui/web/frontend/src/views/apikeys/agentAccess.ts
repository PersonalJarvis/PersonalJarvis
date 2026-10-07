/**
 * How one company reaches the assistant's agents, and what turning it on or
 * off — or switching it between subscription and API key — saves.
 *
 * A company is one entry on the Agents tab. It may run the agents through a
 * subscription login (a vendor CLI), an API key, or this computer. Several
 * companies are on at once; inside one company exactly one access is used.
 * The choices live in `AgentProviderPrefs` as worker-row ids:
 *
 * - two rows (OpenAI: `openai-codex` + `openai`): the access is the row that
 *   is not disabled, so a switch disables the other one;
 * - one dual row (Claude: `claude-api` for both): the access is whether the
 *   row is in `api_only`;
 * - this computer: every local row is on or off together.
 *
 * Pure: unit-tested with plain rows.
 */
import type { AgentProviderPrefs } from "@/lib/agentProviderPrefs";
import { toggled } from "@/lib/agentProviderPrefs";
import type { AgentRowStatus, ProviderFamily } from "@/lib/providerFamilies";
import type { FamilyState } from "./providers/familyState";

export type Access = "subscription" | "api_key" | "local";

export interface FamilyAccess {
  family: ProviderFamily;
  /** The worker row a subscription login runs the agents through. */
  subRow?: AgentRowStatus;
  /** The worker row a company key runs the agents through. */
  keyRow?: AgentRowStatus;
  /** This computer's rows (Ollama, a local server). */
  localRows: AgentRowStatus[];
  /** One row serves both the subscription and the key. */
  dual: boolean;
  choices: Access[];
  access: Access;
  /** The rows the chosen access runs on (one, or every local row). */
  rows: AgentRowStatus[];
  /** The chosen access can run an agent right now. */
  ready: boolean;
  /** On for the agents: chosen, ready and not turned off. */
  on: boolean;
}

function subscriptionRow(rows: AgentRowStatus[]): AgentRowStatus | undefined {
  return rows.find((row) => row.billing === "subscription" || row.billing === "subscription_or_api");
}

function apiRow(rows: AgentRowStatus[]): AgentRowStatus | undefined {
  return rows.find((row) => row.billing === "api") ?? rows.find((row) => row.billing === "subscription_or_api");
}

export function accessReady(access: Access, family: ProviderFamily, state: FamilyState | undefined): boolean {
  if (access === "local") return true;
  if (access === "subscription") return Boolean(state?.subscriptionOn);
  return family.key_present || Boolean(state?.viaProject);
}

export function familyAccess(
  family: ProviderFamily,
  allRows: AgentRowStatus[],
  prefs: AgentProviderPrefs,
  state: FamilyState | undefined,
): FamilyAccess {
  const rows = allRows.filter((row) => family.agent_ids.includes(row.jarvis));
  const localRows = family.local ? rows.filter((row) => row.billing === "local") : [];
  const subRow = family.subscription ? subscriptionRow(rows) : undefined;
  const keyRow = !family.local && family.key_slot ? apiRow(rows) : undefined;
  const dual = Boolean(subRow && keyRow && subRow.jarvis === keyRow.jarvis);
  const choices: Access[] = family.local
    ? ["local"]
    : [...(subRow ? (["subscription"] as const) : []), ...(keyRow ? (["api_key"] as const) : [])];
  const disabled = new Set(prefs.disabled);

  let access: Access = choices[0] ?? "api_key";
  if (subRow && keyRow) {
    if (dual) {
      access = prefs.api_only.includes(keyRow.jarvis) ? "api_key" : "subscription";
    } else {
      const subOn = !disabled.has(subRow.jarvis);
      const keyOn = !disabled.has(keyRow.jarvis);
      if (subOn !== keyOn) access = subOn ? "subscription" : "api_key";
      else if (state?.subscriptionOn) access = "subscription";
      else access = accessReady("api_key", family, state) ? "api_key" : "subscription";
    }
  }

  const chosen = access === "local" ? localRows : [access === "subscription" ? subRow : keyRow].filter(Boolean) as AgentRowStatus[];
  const ready = chosen.length > 0 && accessReady(access, family, state);
  const on = ready && chosen.some((row) => !disabled.has(row.jarvis));
  return { family, subRow, keyRow, localRows, dual, choices, access, rows: chosen, ready, on };
}

function allIds(fa: FamilyAccess): string[] {
  return [...new Set([fa.subRow, fa.keyRow, ...fa.localRows].filter(Boolean).map((row) => row!.jarvis))];
}

/** What turning the company on or off saves. */
export function onPatch(fa: FamilyAccess, on: boolean, prefs: AgentProviderPrefs): Partial<AgentProviderPrefs> {
  if (!on) return { disabled: toggled(prefs.disabled, allIds(fa), false) };
  const chosen = fa.rows.map((row) => row.jarvis);
  const others = allIds(fa).filter((id) => !chosen.includes(id));
  return { disabled: toggled(toggled(prefs.disabled, others, false), chosen, true) };
}

/** What switching the company to `access` saves; the company is on afterwards. */
export function accessPatch(fa: FamilyAccess, access: Access, prefs: AgentProviderPrefs): Partial<AgentProviderPrefs> {
  if (fa.dual && fa.keyRow) {
    const id = fa.keyRow.jarvis;
    return {
      api_only: toggled(prefs.api_only, [id], access !== "api_key"),
      disabled: toggled(prefs.disabled, [id], true),
    };
  }
  const target = access === "subscription" ? fa.subRow : fa.keyRow;
  const other = access === "subscription" ? fa.keyRow : fa.subRow;
  if (!target) return {};
  return {
    disabled: toggled(toggled(prefs.disabled, other ? [other.jarvis] : [], false), [target.jarvis], true),
  };
}

/**
 * Where the tasks the assistant hands off go once `entries` are saved: the
 * current default while it is still on, else the first company that is on
 * (`prefer` first — the one just changed), else nothing to change.
 */
export function nextDefault(entries: FamilyAccess[], current: string, prefer?: string): AgentRowStatus | null {
  const live = entries.filter((fa) => fa.on).flatMap((fa) => fa.rows);
  if (live.some((row) => row.jarvis === current)) return null;
  const preferred = entries.find((fa) => fa.family.id === prefer && fa.on)?.rows[0];
  return preferred ?? live[0] ?? null;
}

/** Saved prefs with `patch` applied, for computing the state that follows a save. */
export function withPatch(prefs: AgentProviderPrefs, patch: Partial<AgentProviderPrefs>): AgentProviderPrefs {
  return { ...prefs, ...patch };
}
