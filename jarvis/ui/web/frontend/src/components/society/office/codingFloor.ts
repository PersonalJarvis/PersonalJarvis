/**
 * The coding floor's roster: every coding-agent pane of the Agentic IDE as a
 * figure in the office, one floor above the society agents.
 *
 * A pane is not a society agent — it has no chat session, no budget and no
 * grants — but the office renders `SocietyAgent`s, so each pane is projected
 * into one here: its call-sign names it, its workspace is its department, its
 * run state picks where it stands, and a hash of the pane's lifetime id
 * (`history_id`) dresses it. The same pane always looks the same; two panes
 * almost never do.
 *
 * Pure except for `useCodingFloorOccupants`, which rides the shared,
 * reference-counted pane poll in `store/workspacePanes` — it adds no timer of
 * its own.
 */
import { useMemo, useRef } from "react";

import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";
import { useWorkspacePanes, useWorkspacePanesStore } from "@/store/workspacePanes";
import { dotKindFor, stateKeyFor, type AgentDotKind, type AgentStateKey } from "@/components/agentic/sidePanel/agentStatus";
import { sessionTitle } from "@/components/agentic/sessionTitle";

import type { AgentRunState, SocietyAgent } from "../data";
import { resolvePalette, type FigureRecipe } from "../figures/figureRecipe";
import { hashString, SKIN_TONES } from "./toyFigureModel";
import { randomLook } from "./wardrobe";

export interface PaneOccupant {
  agent: SocietyAgent;
  pane: WorkspacePaneRow;
  /** The IDE's own four-colour reading of the pane (the side panel's dot). */
  dot: AgentDotKind;
  /** The finer word behind the dot ("starting", "needs_input", "failed", …). */
  stateKey: AgentStateKey;
}

const PANE_ID_PREFIX = "pane:";

/** The office id of a pane's figure: stable for the pane's lifetime, even when its call-sign is reused. */
export function paneAgentId(pane: Pick<WorkspacePaneRow, "workspace_id" | "history_id" | "key">): string {
  return `${PANE_ID_PREFIX}${pane.workspace_id}:${pane.history_id || pane.key}`;
}

export function isPaneAgentId(id: string): boolean {
  return id.startsWith(PANE_ID_PREFIX);
}

/** Coding panes only: a shell is not an agent, and an archived pane is hidden from every list. */
export function isCodingPane(pane: WorkspacePaneRow): boolean {
  return !pane.archived && pane.agent !== "shell";
}

/**
 * The office run state of a pane.
 *
 * working / starting / pending → "working": it sits at its screen.
 * asking → "waiting": it stands at its desk and waves for the person.
 * error / failed → "waiting" as well. The office has no error pose, and the
 * two alternatives are both wrong: "idle" would send a broken session off to
 * the coffee bar as if nothing happened, "paused" would put it to sleep on the
 * couch. A failed session needs the person just like an asking one, so it
 * stands at its desk; `PaneOccupant.dot` / `stateKey` still say "error", so
 * the panel and name plate can show that word instead of "waiting".
 * idle / done / exited → "idle": it wanders.
 */
export function paneRunState(pane: WorkspacePaneRow, dot: AgentDotKind = dotKindFor(pane)): AgentRunState {
  if (dot === "working") return "working";
  if (dot === "idle") return "idle";
  return "waiting";
}

/** "Claude Code" → "Claude", "Gemini CLI" → "Gemini"; the agent id when the pane names no CLI. */
export function shortAgentName(pane: Pick<WorkspacePaneRow, "display_name" | "agent">): string {
  const first = pane.display_name.trim().split(/\s+/)[0] ?? "";
  if (first) return first;
  const agent = pane.agent.trim();
  return agent ? agent.charAt(0).toUpperCase() + agent.slice(1) : "Agent";
}

/** The CLI and the pane's call-sign, "Claude · T2". */
export function paneLabel(pane: Pick<WorkspacePaneRow, "display_name" | "agent" | "name" | "key">): string {
  return `${shortAgentName(pane)} · ${pane.name || pane.key}`;
}

/**
 * The name plate: what the pane is working ON — the same title the IDE draws
 * in the pane's header — so a floor of figures reads as a list of jobs, not
 * "T1, T2, T3". A pane asked nothing yet has no topic and keeps its call-sign.
 */
export function panePlateName(
  pane: Pick<WorkspacePaneRow, "recap" | "last_prompt" | "display_name" | "agent" | "name" | "key">,
): string {
  const title = sessionTitle(pane);
  return title && title !== pane.display_name && title !== pane.name ? title : paneLabel(pane);
}

/** The two halves of a plate: what the pane is about, and where it stands. */
export interface PlateTitle {
  subject: string;
  result: string;
}

/** " — ", " – " or a spaced " - ": the break the recap contract asks the model for. */
const TITLE_BREAK = /\s+[—–-]\s+/;

/**
 * A pane title split for a two-line plate.
 *
 * Recaps are written as "subject — result" ("Office map camera — controls
 * fix"), and one line clipped the result away: four plates read "Office
 * screens — live worker te…", so the half that told the panes apart was
 * the half nobody could see. The subject goes on top, the result below it,
 * each with its own width. A title with no break stays one line.
 */
export function plateTitle(title: string): PlateTitle {
  const text = title.trim();
  const found = TITLE_BREAK.exec(text);
  if (!found) return { subject: text, result: "" };
  const subject = text.slice(0, found.index).trim();
  const result = text.slice(found.index + found[0].length).trim();
  return subject && result ? { subject, result } : { subject: text, result: "" };
}

function workspaceLabel(pane: WorkspacePaneRow): string {
  const name = pane.workspace_name.trim();
  if (name) return name;
  const folder = pane.folder.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  return folder || "Workspace";
}

const BASE_RECIPE: FigureRecipe = { contract: 1, archetype: "biped", base: "rogue", parts: {}, palette: {} };

/**
 * A random but stable look per pane: outfit, colourway, hair style and colour,
 * eyewear (the wardrobe's `randomLook`), plus a skin tone of its own — all from
 * the pane's `history_id`, so it survives a call-sign being reused.
 */
export function paneFigure(historyId: string): FigureRecipe {
  const seed = `coding:${historyId || "pane"}`;
  const look = randomLook(BASE_RECIPE, seed);
  const skin = SKIN_TONES[(Math.imul(hashString(seed) ^ 0x5151, 0x9e3779b1) >>> 0) % SKIN_TONES.length];
  return { ...look, palette: { ...look.palette, skin } };
}

function toAgent(pane: WorkspacePaneRow, department: string, dot: AgentDotKind): SocietyAgent {
  const figure = paneFigure(pane.history_id || `${pane.workspace_id}:${pane.key}`);
  const palette = resolvePalette(figure);
  return {
    agentId: paneAgentId(pane),
    name: panePlateName(pane),
    title: paneLabel(pane),
    description: pane.last_prompt,
    tier: "specialist",
    provider: pane.agent,
    providerLabel: department,
    model: "",
    effort: "",
    accountId: pane.account ?? undefined,
    computerId: null,
    figure,
    palette: { primary: palette.primary, secondary: palette.secondary, accent: palette.accent },
    grantMode: "all",
    toolGrants: [],
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    permissionCeiling: "ask",
    dailyBudgetUsd: 0,
    checkpoint: "idle",
    state: paneRunState(pane, dot),
    lifecycle: "active",
    createdMs: (pane.started_at ?? 0) * 1000,
    maxConcurrentRuns: 1,
    workspaceDir: pane.folder,
    wikiNamespace: "",
    chatSessionId: null,
    routines: [],
    stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: pane.last_prompt_at ? pane.last_prompt_at * 1000 : null },
  };
}

function byWorkspaceThenArrival(a: WorkspacePaneRow, b: WorkspacePaneRow): number {
  return workspaceLabel(a).localeCompare(workspaceLabel(b))
    || a.workspace_id.localeCompare(b.workspace_id)
    || (a.started_at ?? Infinity) - (b.started_at ?? Infinity)
    || a.name.localeCompare(b.name, undefined, { numeric: true })
    || a.history_id.localeCompare(b.history_id);
}

/**
 * The department label of every workspace. Two open workspaces with the same
 * name would otherwise share one department; the later one gets " 2", " 3".
 */
function departmentLabels(panes: readonly WorkspacePaneRow[]): Map<string, string> {
  const labels = new Map<string, string>();
  const used = new Map<string, number>();
  for (const pane of panes) {
    if (labels.has(pane.workspace_id)) continue;
    const base = workspaceLabel(pane);
    const n = (used.get(base) ?? 0) + 1;
    used.set(base, n);
    labels.set(pane.workspace_id, n === 1 ? base : `${base} ${n}`);
  }
  return labels;
}

/** Every coding pane as an office occupant, ordered by workspace, then start time, then call-sign. */
export function paneOccupants(panes: readonly WorkspacePaneRow[]): PaneOccupant[] {
  const coding = panes.filter(isCodingPane).sort(byWorkspaceThenArrival);
  const departments = departmentLabels(coding);
  return coding.map((pane) => {
    const dot = dotKindFor(pane);
    return { agent: toAgent(pane, departments.get(pane.workspace_id)!, dot), pane, dot, stateKey: stateKeyFor(pane, dot) };
  });
}

/**
 * What an occupant is built from. `last_output_at` ticks with every byte a
 * pane prints and changes nothing on the floor, so it is left out: a busy
 * terminal must not rebuild its figure four times a second.
 */
function occupantSignature(pane: WorkspacePaneRow, department: string): string {
  return `${department}|${JSON.stringify({ ...pane, last_output_at: null })}`;
}

export interface CodingFloorOccupants {
  occupants: PaneOccupant[];
  byAgentId: ReadonlyMap<string, PaneOccupant>;
  /** False until the pane list has answered once: an empty floor before that means "not yet". */
  loaded: boolean;
}

/**
 * The coding floor's roster, live. Subscribes to the shared pane poll only
 * while `enabled`; an occupant whose pane did not change keeps its object
 * identity across polls, and the list keeps its identity when nothing changed.
 */
export function useCodingFloorOccupants(enabled: boolean): CodingFloorOccupants {
  const panes = useWorkspacePanes(enabled);
  const loaded = useWorkspacePanesStore((s) => s.loaded);
  const cache = useRef<{ bySignature: Map<string, PaneOccupant>; result: CodingFloorOccupants | null }>({
    bySignature: new Map(), result: null,
  });
  return useMemo(() => {
    const fresh = paneOccupants(panes);
    const previous = cache.current;
    const bySignature = new Map<string, PaneOccupant>();
    const occupants = fresh.map((occupant) => {
      const signature = occupantSignature(occupant.pane, occupant.agent.providerLabel);
      const kept = previous.bySignature.get(signature) ?? occupant;
      bySignature.set(signature, kept);
      return kept;
    });
    const last = previous.result;
    const unchanged = last !== null && last.loaded === loaded && last.occupants.length === occupants.length
      && last.occupants.every((o, i) => o === occupants[i]);
    const result = unchanged ? last : {
      occupants,
      byAgentId: new Map(occupants.map((o) => [o.agent.agentId, o])),
      loaded,
    };
    cache.current = { bySignature, result };
    return result;
  }, [panes, loaded]);
}
