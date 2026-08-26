/**
 * "Set up everything" — the one-click flow behind the Local models overview,
 * written as a pure async function so it can be tested against a faked
 * `fetch` and driven from a hook (`useLocalSetup`).
 *
 * The steps, in order, each reported through `report`:
 *
 *  1. server   — the overview is read LIVE. A local server that is installed
 *                but stopped is started; one that is not installed is
 *                installed (the button that starts this flow names the
 *                install, so the click is the consent) and then started; a
 *                remote server that does not answer stops the flow with the
 *                backend's sentence.
 *  2. plan     — the roles' recommendations are read from that live overview,
 *                so they come from what is on the server NOW: the best
 *                installed download per role, a download only where nothing
 *                installed qualifies.
 *  3. pull     — every recommended model that is missing is downloaded once.
 *  4. assign   — each writable role that is not already on its pick is set.
 *  5. tune     — the suggested options are written for every model assigned.
 *  6. verify   — three real round trips (`POST …/verify`): the server, one
 *                chat answer, one embedding — so "set up" is a tested sentence.
 *  7. save     — "start the server with Jarvis" is switched on for a local
 *                server, so the next start needs no click at all.
 *
 * A role that is served elsewhere (the wiki embedding on a cloud provider,
 * the voice server not installed) or has no pick is skipped and named in
 * the summary; a role whose write fails is skipped with the reason and the
 * flow goes on — one refused slot must not undo the other four.
 *
 * Nothing here touches react-query or i18n: the caller repaints and renders
 * the sentences.
 */
import {
  ollamaRuntime,
  ollamaRuntimeInstall,
  ollamaRuntimeStart,
} from "@/hooks/useProviders";
import {
  getOverview,
  modelPullStatus,
  setAutostart,
  setRole,
  startModelPull,
  verifySetup,
  type LocalModelRole,
  type OverviewResponse,
  type RoleRow,
  type VerifyResponse,
} from "@/hooks/useLocalModels";

/** The five slots the flow writes, in the order the rows show them. */
export const SETUP_ROLES: readonly LocalModelRole[] = [
  "chat",
  "voice",
  "tools_screen",
  "deep",
  "embedding",
];

export type SetupStep =
  | {
      phase: "server";
      action: "starting" | "installing";
      percent?: number;
      detail?: string;
    }
  | { phase: "planning" }
  | { phase: "pulling"; model: string; percent?: number; message?: string }
  | { phase: "assigning"; role: LocalModelRole; model: string }
  | { phase: "tuning"; model: string }
  | { phase: "verifying" }
  | { phase: "saving" }
  | { phase: "done"; summary: SetupSummary }
  | { phase: "error"; message: string; summary?: SetupSummary };

export interface SetupSummary {
  /** Roles written in this run, in order. */
  assigned: Array<{ role: LocalModelRole; model: string }>;
  /** Roles that were already on their pick. */
  kept: Array<{ role: LocalModelRole; model: string }>;
  /** Roles left alone; `note` is the backend's sentence, "" = no pick. */
  skipped: Array<{ role: LocalModelRole; note: string }>;
  /** Models downloaded in this run. */
  pulled: string[];
  /** One readback sentence per model tuned. */
  readbacks: Record<string, string>;
  /** The server was started (or installed and started) by this run. */
  serverStarted: boolean;
  /** The proof: the server, one chat answer, one embedding. */
  verify?: VerifyResponse;
  /** "Start with Jarvis" was switched on (local servers only). */
  autostart?: boolean;
}

export interface RunLocalSetupOptions {
  providerId: string;
  report: (step: SetupStep) => void;
  /** False once the caller unmounted; the flow then stops writing. */
  alive: () => boolean;
  /** Writes the suggested options for one model; answers the readback. */
  tune: (model: string) => Promise<string>;
  /** Milliseconds between polls of a download or an install (tests: 0). */
  pollMs?: number;
}

/** "qwen3.5" and "qwen3.5:latest" are the same download. */
export function canonical(name: string): string {
  return name.endsWith(":latest") ? name.slice(0, -":latest".length) : name;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/** Poll one download until the server reports it done; throws on error. */
export async function waitForPull(
  providerId: string,
  model: string,
  onProgress: (p: { percent?: number; message: string }) => void,
  alive: () => boolean,
  pollMs = 2000,
): Promise<void> {
  await startModelPull(providerId, model);
  for (;;) {
    if (!alive()) return;
    const pull = await modelPullStatus(providerId, model);
    if (pull.state === "done") return;
    if (pull.state === "error")
      throw new Error(pull.message || "download failed");
    onProgress({ percent: pull.percent, message: pull.message });
    await sleep(pollMs);
  }
}

/** Install the runtime and wait for the installer to finish; throws on error. */
async function installRuntime(
  providerId: string,
  report: (step: SetupStep) => void,
  alive: () => boolean,
  pollMs: number,
): Promise<void> {
  const first = await ollamaRuntimeInstall(providerId);
  if (first.phase === "error" || (first.started === false && first.error))
    throw new Error(first.error || first.message || "install failed");
  report({
    phase: "server",
    action: "installing",
    percent: first.percent,
    detail: first.detail,
  });
  for (;;) {
    if (!alive()) return;
    const { install } = await ollamaRuntime(providerId);
    if (!install.running) {
      if (install.phase === "error")
        throw new Error(install.error || "install failed");
      return;
    }
    report({
      phase: "server",
      action: "installing",
      percent: install.percent,
      detail: install.detail,
    });
    await sleep(pollMs);
  }
}

/**
 * Make sure the server answers; returns true when this run started it.
 * A remote server is only checked — nothing can be started there.
 */
async function ensureServer(
  overview: OverviewResponse,
  providerId: string,
  report: (step: SetupStep) => void,
  alive: () => boolean,
  pollMs: number,
): Promise<boolean> {
  const s = overview.server;
  if (s.running) return false;
  if (s.host_kind === "remote")
    throw new Error(
      s.error || s.detail || `The server at ${s.base_url} does not answer.`,
    );
  if (!s.installed) {
    await installRuntime(providerId, report, alive, pollMs);
    if (!alive()) return false;
  }
  report({ phase: "server", action: "starting" });
  await ollamaRuntimeStart(providerId);
  return true;
}

/** The plan: which role gets which model, what is missing, what is kept. */
export function planRoles(overview: OverviewResponse): {
  assign: Array<{ role: LocalModelRole; model: string }>;
  kept: SetupSummary["kept"];
  skipped: SetupSummary["skipped"];
  pulls: string[];
} {
  const installed = new Set(
    overview.inventory.models.map((m) => canonical(m.name)),
  );
  const byId = new Map<string, RoleRow>(
    overview.roles.roles.map((r) => [r.id, r]),
  );
  const assign: Array<{ role: LocalModelRole; model: string }> = [];
  const kept: SetupSummary["kept"] = [];
  const skipped: SetupSummary["skipped"] = [];
  const pulls: string[] = [];
  for (const role of SETUP_ROLES) {
    const row = byId.get(role);
    if (!row || !row.writable) continue;
    // Served by something other than this server (the note says what):
    // that is a choice the user made elsewhere, not a gap to fill.
    if (row.note && !row.current) {
      skipped.push({ role, note: row.note });
      continue;
    }
    const model = row.recommended;
    if (!model) {
      skipped.push({ role, note: "" });
      continue;
    }
    if (row.current && canonical(row.current) === canonical(model) && row.installed) {
      kept.push({ role, model });
      continue;
    }
    assign.push({ role, model });
    if (!installed.has(canonical(model)) && !pulls.includes(model))
      pulls.push(model);
  }
  return { assign, kept, skipped, pulls };
}

export async function runLocalSetup({
  providerId,
  report,
  alive,
  tune,
  pollMs = 2000,
}: RunLocalSetupOptions): Promise<SetupSummary> {
  const summary: SetupSummary = {
    assigned: [],
    kept: [],
    skipped: [],
    pulled: [],
    readbacks: {},
    serverStarted: false,
  };
  report({ phase: "planning" });
  let overview = await getOverview(providerId, true);
  if (!alive()) return summary;
  summary.serverStarted = await ensureServer(
    overview,
    providerId,
    report,
    alive,
    pollMs,
  );
  if (!alive()) return summary;
  if (summary.serverStarted) {
    // The recommendations must come from the server that now answers.
    report({ phase: "planning" });
    overview = await getOverview(providerId, true);
    if (!alive()) return summary;
  }
  if (overview.inventory.error)
    throw new Error(overview.inventory.error);

  const plan = planRoles(overview);
  summary.kept = plan.kept;
  summary.skipped = plan.skipped;

  for (const model of plan.pulls) {
    report({ phase: "pulling", model, percent: 0, message: "" });
    await waitForPull(
      providerId,
      model,
      (p) => report({ phase: "pulling", model, ...p }),
      alive,
      pollMs,
    );
    if (!alive()) return summary;
    summary.pulled.push(model);
  }

  const tuned = new Set<string>();
  for (const { role, model } of plan.assign) {
    report({ phase: "assigning", role, model });
    try {
      await setRole(providerId, role, model);
    } catch (err) {
      // One refused slot (a voice server that is not installed, a write the
      // config guard turned down) must not undo the other four.
      summary.skipped.push({
        role,
        note: err instanceof Error ? err.message : String(err),
      });
      continue;
    }
    if (!alive()) return summary;
    summary.assigned.push({ role, model });
    if (tuned.has(model)) continue;
    tuned.add(model);
    report({ phase: "tuning", model });
    summary.readbacks[model] = await tune(model);
    if (!alive()) return summary;
  }

  // The proof — always, even when nothing changed: "set up" must be a
  // sentence that was tested this very minute.
  report({ phase: "verifying" });
  summary.verify = await verifySetup(providerId);
  if (!alive()) return summary;

  // Saved for next time: a local server comes up with Jarvis from now on.
  if (overview.server.host_kind !== "remote") {
    report({ phase: "saving" });
    try {
      await setAutostart(providerId, true);
      summary.autostart = true;
    } catch (err) {
      // The roles and the proof stand; only the boot convenience is unsaved.
      console.warn("[local-models] autostart not saved", err);
      summary.autostart = false;
    }
    if (!alive()) return summary;
  }
  report({ phase: "done", summary });
  return summary;
}
