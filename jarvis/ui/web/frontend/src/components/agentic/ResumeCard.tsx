/**
 * The restore manifest shown before the Agentic IDE reopens a saved working set.
 *
 * This is intentionally closer to a flight manifest than a generic card: one
 * asymmetric decision header, one strip of real recovery facts, then dense
 * workspace and agent detail. A large fleet stays readable because terminals
 * collapse into a short preview and expand inside a bounded scroll region.
 *
 * ## Every amber here is a pair
 *
 * "Cannot reopen" was written as `amber-200`/`amber-300` throughout — the pale
 * end of the ramp, which is a warning on the dark app and very nearly paper on
 * the light one. On the light theme this screen therefore reported its one
 * genuinely bad outcome in ink the reader could not see (maintainer,
 * 2026-08-11).
 *
 * Each of those now carries a light partner in the pairing the rest of the app
 * already uses (`text-amber-800 dark:text-amber-200`, and the equivalents for
 * fills and borders). The gold "continues" cue lost its `/85` for the same
 * reason: `--primary` is tuned to 5.1:1 on paper, and the alpha was spending
 * a fifth of that on nothing.
 */
import { useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Clock3,
  FolderOpen,
  LayoutGrid,
  MessageSquare,
  RotateCcw,
  Trash2,
} from "lucide-react";

import { useT } from "@/i18n";
import type {
  ResumeOffer,
  ResumeTerminalOffer,
  ResumeWorkspaceOffer,
} from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { AgentMark } from "./AgentMark";
import { Button, SectionLabel } from "./controls";

interface ResumeCardProps {
  offer: ResumeOffer;
  busy: boolean;
  onResume: () => void;
  onDismiss: () => void;
}

type Translator = (key: string) => string;

/** Full terminal tiles shown before the rest moves behind "show all". */
const TERMINALS_SHOWN = 12;

function fill(
  template: string,
  values: Record<string, string | number>,
): string {
  return Object.entries(values).reduce(
    (text, [key, value]) => text.replaceAll(`{${key}}`, String(value)),
    template,
  );
}

/** Coarse relative time: enough to identify the session, never false precision. */
export function savedAgo(
  seconds: number,
  now: number = Date.now(),
  t?: Translator,
): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  const minutes = Math.max(0, Math.floor((now - seconds * 1000) / 60_000));
  if (minutes < 1)
    return t ? t("workspace_launcher.resume.just_now") : "just now";
  if (minutes < 60) {
    return t
      ? fill(t("workspace_launcher.resume.minutes_ago"), { count: minutes })
      : `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return t
      ? fill(t("workspace_launcher.resume.hours_ago"), { count: hours })
      : `${hours} hour${hours === 1 ? "" : "s"} ago`;
  }
  const days = Math.floor(hours / 24);
  return t
    ? fill(t("workspace_launcher.resume.days_ago"), { count: days })
    : `${days} day${days === 1 ? "" : "s"} ago`;
}

function countLabel(
  count: number,
  singular: string,
  plural: string,
  t?: Translator,
): string {
  if (!t) return `${count} ${count === 1 ? singular : plural}`;
  return fill(
    t(`workspace_launcher.resume.${count === 1 ? singular : plural}`),
    { count },
  );
}

/** One honest line for what the restore action will actually recover. */
export function resumeSummary(offer: ResumeOffer, t?: Translator): string {
  const folders = countLabel(offer.workspace_count, "folder", "folders", t);
  const terminals = countLabel(
    offer.terminal_count,
    "terminal",
    "terminals",
    t,
  );
  if (!offer.available) {
    const anyFolder = offer.workspaces.some(
      (workspace) => workspace.folder_exists,
    );
    return t
      ? t(
          anyFolder
            ? "workspace_launcher.resume.summary_unavailable_clis"
            : "workspace_launcher.resume.summary_unavailable_folders",
        )
      : anyFolder
        ? "None of these terminals can run here — their coding CLIs are not installed."
        : "Those folders are no longer on this machine.";
  }
  const kept = offer.resumable_count;
  const incomingPanes = offer.workspaces
    .filter((workspace) => workspace.in_last_session !== false)
    .flatMap((workspace) => workspace.terminals);
  const unavailable = incomingPanes.filter((pane) => !pane.available).length;
  const fresh = incomingPanes.length
    ? incomingPanes.filter((pane) => pane.available && !pane.resumable).length
    : Math.max(0, offer.terminal_count - kept);
  const unavailableSuffix = unavailable
    ? t
      ? ` ${fill(t("workspace_launcher.resume.summary_unavailable_suffix"), {
          count: unavailable,
        })}`
      : ` ${unavailable} cannot reopen on this machine.`
    : "";
  if (kept === 0) {
    const summary = t
      ? fill(t("workspace_launcher.resume.summary_none"), {
          folders,
          terminals,
        })
      : `${folders}, ${terminals} — names and places come back; none of their conversations could be kept.`;
    return `${summary}${unavailableSuffix}`;
  }
  if (kept === offer.terminal_count && unavailable === 0) {
    return t
      ? fill(t("workspace_launcher.resume.summary_all"), { folders, terminals })
      : `${folders}, ${terminals}, each continuing the conversation it was having.`;
  }
  const summary = t
    ? fill(t("workspace_launcher.resume.summary_some"), {
        folders,
        terminals,
        resumable: kept,
        fresh,
      })
    : `${folders}, ${terminals} — ${kept} continue their conversation, ${fresh} start fresh.`;
  return `${summary}${unavailableSuffix}`;
}

export interface AgentFleetItem {
  agent: string;
  displayName: string;
  total: number;
  resumable: number;
  fresh: number;
  unavailable: number;
  prompts: number;
}

/** Aggregate a registry-driven terminal list without hardcoding provider names. */
export function summarizeAgentFleet(
  workspaces: ResumeWorkspaceOffer[],
): AgentFleetItem[] {
  const fleet = new Map<string, AgentFleetItem>();
  for (const workspace of workspaces) {
    for (const pane of workspace.terminals) {
      const current = fleet.get(pane.agent) ?? {
        agent: pane.agent,
        displayName: pane.display_name || pane.agent,
        total: 0,
        resumable: 0,
        fresh: 0,
        unavailable: 0,
        prompts: 0,
      };
      current.total += 1;
      current.prompts += pane.prompts_sent;
      if (!pane.available) current.unavailable += 1;
      else if (pane.resumable) current.resumable += 1;
      else current.fresh += 1;
      fleet.set(pane.agent, current);
    }
  }
  return [...fleet.values()].sort(
    (left, right) =>
      right.total - left.total ||
      left.displayName.localeCompare(right.displayName),
  );
}

function paneState(pane: ResumeTerminalOffer, t: Translator): string {
  if (!pane.available) return t("workspace_launcher.resume.not_installed");
  return pane.resumable
    ? t("workspace_launcher.resume.continues")
    : t("workspace_launcher.resume.starts_fresh");
}

function PromptCount({ count, t }: { count: number; t: Translator }) {
  return (
    <span>
      {fill(
        t(
          count === 1
            ? "workspace_launcher.resume.prompt_one"
            : "workspace_launcher.resume.prompt_many",
        ),
        { count },
      )}
    </span>
  );
}

function TerminalTile({
  pane,
  t,
}: {
  pane: ResumeTerminalOffer;
  t: Translator;
}) {
  const status = paneState(pane, t);
  const position = fill(t("workspace_launcher.resume.position"), {
    column: pane.column + 1,
    row: pane.slot + 1,
  });
  const description = fill(
    t("workspace_launcher.resume.terminal_description"),
    {
      name: pane.name,
      agent: pane.display_name,
      status,
      prompts: pane.prompts_sent,
      position,
    },
  );

  return (
    <li
      data-testid={`resume-pane-${pane.key}`}
      aria-label={description}
      title={description}
      className={cn(
        "group relative grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-2.5 border border-border/60 bg-background/35 p-2.5 transition-colors hover:border-border hover:bg-background/70",
        pane.resumable && pane.available && "border-primary/25",
        !pane.available && "border-amber-600/45 dark:border-amber-400/25",
      )}
    >
      <span
        className={cn(
          "absolute inset-y-0 left-0 w-0.5",
          !pane.available
            ? "bg-amber-600/80 dark:bg-amber-300/70"
            : pane.resumable
              ? "bg-primary/80"
              : "bg-muted-foreground/35",
        )}
      />
      <AgentMark agent={pane.agent} label={pane.display_name} size="sm" />
      <div className="min-w-0">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <span className="truncate font-mono text-[11px] font-semibold text-foreground">
            {pane.name}
          </span>
          <span
            className={cn(
              "shrink-0 text-[9px] font-medium uppercase tracking-[0.12em]",
              !pane.available
                ? "text-amber-800 dark:text-amber-200"
                : pane.resumable
                  ? "text-primary"
                  : "text-muted-foreground",
            )}
          >
            {status}
          </span>
        </div>
        <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
          {pane.display_name}
        </p>
        <div className="mt-1.5 flex items-center justify-between gap-2 font-mono text-[9px] text-muted-foreground/75">
          <PromptCount count={pane.prompts_sent} t={t} />
          <span>{position}</span>
        </div>
      </div>
    </li>
  );
}

function WorkspaceRow({
  space,
  t,
}: {
  space: ResumeWorkspaceOffer;
  t: Translator;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded
    ? space.terminals
    : space.terminals.slice(0, TERMINALS_SHOWN);
  const hidden = Math.max(0, space.terminals.length - TERMINALS_SHOWN);
  const fresh = space.terminals.filter(
    (pane) => pane.available && !pane.resumable,
  ).length;
  const unavailable = space.terminals.filter((pane) => !pane.available).length;
  const fleet = summarizeAgentFleet([space]);
  const when = savedAgo(space.saved_at ?? 0, Date.now(), t);
  const total = Math.max(1, space.terminals.length);

  return (
    <li
      data-testid={`resume-workspace-${space.folder_name}`}
      className={cn(
        "border-t border-border/70 py-5",
        space.in_last_session === false && "opacity-70",
      )}
    >
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-control border border-border bg-background/60 text-muted-foreground">
            <FolderOpen className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h4 className="truncate text-sm font-semibold text-foreground">
                {space.name || space.folder_name}
              </h4>
              {when && (
                <span className="inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                  <Clock3 className="h-3 w-3" />
                  {when}
                </span>
              )}
            </div>
            <code className="mt-1 block truncate font-mono text-[10px] text-muted-foreground">
              {space.folder}
            </code>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {fleet.map((item) => (
                <span
                  key={item.agent}
                  className="inline-flex items-center gap-1.5 border border-border/60 bg-background/45 px-2 py-1 text-[10px] text-muted-foreground"
                >
                  <AgentMark
                    agent={item.agent}
                    label={item.displayName}
                    size="sm"
                    className="h-5 w-5 border-0 bg-transparent"
                  />
                  <span>{item.displayName}</span>
                  <strong className="font-mono font-semibold text-foreground">
                    {item.total}
                  </strong>
                </span>
              ))}
            </div>
            {!space.folder_exists && (
              <p className="mt-2 flex items-center gap-1.5 text-xs leading-relaxed text-amber-800 dark:text-amber-200">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                {t("workspace_launcher.resume.folder_missing")}
              </p>
            )}
          </div>
        </div>

        <div className="min-w-[220px] lg:text-right">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[10px] text-muted-foreground lg:justify-end">
            <strong className="font-mono text-xs font-semibold tabular-nums text-foreground">
              {fill(t("workspace_launcher.resume.terminal_count"), {
                count: space.terminals.length,
              })}
            </strong>
            {space.resumable_count > 0 && (
              <span>
                {fill(t("workspace_launcher.resume.continuing_count"), {
                  count: space.resumable_count,
                })}
              </span>
            )}
            {fresh > 0 && (
              <span>
                {fill(t("workspace_launcher.resume.fresh_count"), {
                  count: fresh,
                })}
              </span>
            )}
            {unavailable > 0 && (
              <span className="text-amber-800 dark:text-amber-200">
                {fill(t("workspace_launcher.resume.unavailable_count"), {
                  count: unavailable,
                })}
              </span>
            )}
          </div>
          <div
            className="mt-2 flex h-1.5 overflow-hidden bg-muted/70"
            aria-label={fill(t("workspace_launcher.resume.recovery_bar"), {
              continuing: space.resumable_count,
              fresh,
              unavailable,
            })}
          >
            <span
              className="bg-primary/80"
              style={{ width: `${(space.resumable_count / total) * 100}%` }}
            />
            <span
              className="bg-muted-foreground/35"
              style={{ width: `${(fresh / total) * 100}%` }}
            />
            <span
              className="bg-amber-600/80 dark:bg-amber-300/70"
              style={{ width: `${(unavailable / total) * 100}%` }}
            />
          </div>
        </div>
      </div>

      <ul
        data-testid={`resume-terminal-grid-${space.folder_name}`}
        className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
      >
        {visible.map((pane) => (
          <TerminalTile key={pane.key} pane={pane} t={t} />
        ))}
      </ul>

      {hidden > 0 && (
        <button
          type="button"
          data-testid={`resume-more-${space.folder_name}`}
          onClick={() => setExpanded((open) => !open)}
          className="mt-3 inline-flex min-h-8 items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          aria-expanded={expanded}
        >
          {expanded ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
          {expanded
            ? t("workspace_launcher.resume.show_less")
            : fill(t("workspace_launcher.resume.show_all"), {
                count: space.terminals.length,
                hidden,
              })}
        </button>
      )}
    </li>
  );
}

function FleetManifest({
  fleet,
  t,
}: {
  fleet: AgentFleetItem[];
  t: Translator;
}) {
  return (
    <aside className="border-t border-border/70 pt-5 xl:border-l xl:border-t-0 xl:pl-5 xl:pt-0">
      <SectionLabel>{t("workspace_launcher.resume.agent_fleet")}</SectionLabel>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("workspace_launcher.resume.agent_fleet_hint")}
      </p>
      <ul className="mt-4 divide-y divide-border/60 border-y border-border/60">
        {fleet.map((item) => {
          const total = Math.max(1, item.total);
          return (
            <li key={item.agent} className="py-3">
              <div className="flex items-center gap-2.5">
                <AgentMark agent={item.agent} label={item.displayName} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-xs font-semibold text-foreground">
                      {item.displayName}
                    </span>
                    <span className="font-mono text-sm font-semibold tabular-nums text-foreground">
                      {item.total}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    {fill(t("workspace_launcher.resume.fleet_detail"), {
                      continuing: item.resumable,
                      fresh: item.fresh,
                      prompts: item.prompts,
                    })}
                    {item.unavailable > 0 && (
                      <span className="text-amber-800 dark:text-amber-200">
                        {` · ${fill(
                          t("workspace_launcher.resume.fleet_unavailable"),
                          { count: item.unavailable },
                        )}`}
                      </span>
                    )}
                  </p>
                </div>
              </div>
              <div className="mt-2 flex h-1 overflow-hidden bg-muted/70">
                <span
                  className="bg-primary/80"
                  style={{ width: `${(item.resumable / total) * 100}%` }}
                />
                <span
                  className="bg-muted-foreground/35"
                  style={{ width: `${(item.fresh / total) * 100}%` }}
                />
                <span
                  className="bg-amber-600/80 dark:bg-amber-300/70"
                  style={{ width: `${(item.unavailable / total) * 100}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>

      <SectionLabel className="mt-6">
        {t("workspace_launcher.resume.restore_plan")}
      </SectionLabel>
      <ul className="mt-3 space-y-3 text-xs text-muted-foreground">
        <li className="flex gap-2.5">
          <LayoutGrid className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary/80" />
          <span>{t("workspace_launcher.resume.layout_fact")}</span>
        </li>
        <li className="flex gap-2.5">
          <MessageSquare className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary/80" />
          <span>{t("workspace_launcher.resume.conversation_fact")}</span>
        </li>
        <li className="flex gap-2.5">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-800 dark:text-amber-200" />
          <span>{t("workspace_launcher.resume.availability_fact")}</span>
        </li>
      </ul>
    </aside>
  );
}

function EarlierWorkspaces({
  spaces,
  t,
}: {
  spaces: ResumeWorkspaceOffer[];
  t: Translator;
}) {
  if (spaces.length === 0) return null;
  return (
    <details
      className="border-t border-border/70 py-4"
      data-testid="resume-earlier"
    >
      <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
        {fill(t("workspace_launcher.resume.earlier_title"), {
          count: spaces.length,
        })}
      </summary>
      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("workspace_launcher.resume.earlier_hint")}
      </p>
      <ul className="mt-3 divide-y divide-border/50 border-y border-border/50">
        {spaces.map((space, index) => (
          <li
            key={space.session_id || `${space.folder}#${index}`}
            className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
          >
            <div className="min-w-0">
              <strong className="block truncate text-xs font-semibold text-foreground">
                {space.name || space.folder_name}
              </strong>
              <code className="mt-0.5 block truncate font-mono text-[9px] text-muted-foreground">
                {space.folder}
              </code>
            </div>
            <span className="font-mono text-[10px] text-muted-foreground">
              {fill(t("workspace_launcher.resume.terminal_count"), {
                count: space.terminals.length,
              })}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

export function ResumeCard({
  offer,
  busy,
  onResume,
  onDismiss,
}: ResumeCardProps) {
  const t = useT();
  const when = savedAgo(offer.saved_at, Date.now(), t);
  const coming = offer.workspaces.filter(
    (workspace) => workspace.in_last_session !== false,
  );
  const earlier = offer.workspaces.filter(
    (workspace) => workspace.in_last_session === false,
  );
  const fleet = summarizeAgentFleet(coming);
  const fresh = coming.reduce(
    (count, workspace) =>
      count +
      workspace.terminals.filter((pane) => pane.available && !pane.resumable)
        .length,
    0,
  );
  const unavailable = coming.reduce(
    (count, workspace) =>
      count + workspace.terminals.filter((pane) => !pane.available).length,
    0,
  );
  const restorableWorkspaces = coming.filter(
    (workspace) => workspace.available,
  ).length;

  return (
    <section
      data-testid="resume-card"
      aria-labelledby="resume-card-title"
      className="overflow-hidden border-y border-primary/30 bg-primary/[0.025] px-4 py-5 sm:px-5"
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px] lg:items-stretch">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <SectionLabel className="text-primary/85">
              {t("workspace_launcher.resume.eyebrow")}
            </SectionLabel>
            {when && (
              <span className="inline-flex items-center gap-1 font-mono text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                <Clock3 className="h-3 w-3" />
                {fill(t("workspace_launcher.resume.last_saved"), {
                  time: when,
                })}
              </span>
            )}
          </div>
          <h3
            id="resume-card-title"
            className="mt-2 max-w-2xl text-[clamp(1.25rem,2.2vw,2rem)] font-semibold tracking-[-0.035em] text-foreground"
          >
            {t("workspace_launcher.resume.title")}
          </h3>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">
            {resumeSummary(offer, t)}
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[10px] text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 bg-primary" />
              {t("workspace_launcher.resume.legend_continues")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 bg-muted-foreground/50" />
              {t("workspace_launcher.resume.legend_fresh")}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 bg-amber-600/90 dark:bg-amber-300/80" />
              {t("workspace_launcher.resume.legend_unavailable")}
            </span>
          </div>
        </div>

        <div className="flex flex-col justify-between border-t border-border/70 pt-5 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
          <div className="flex items-end gap-3">
            <span className="font-mono text-5xl font-semibold leading-none tracking-[-0.08em] text-foreground">
              {offer.terminal_count}
            </span>
            <span className="max-w-28 pb-1 text-[10px] uppercase leading-snug tracking-[0.14em] text-muted-foreground">
              {t("workspace_launcher.resume.saved_terminal_fleet")}
            </span>
          </div>
          <div className="mt-5 grid gap-2">
            <Button
              variant="primary"
              data-testid="resume-all"
              disabled={busy || !offer.available}
              onClick={onResume}
              className="h-10 w-full justify-between px-4"
            >
              <span>
                {busy
                  ? t("workspace_launcher.resume.resuming")
                  : fill(
                      t(
                        restorableWorkspaces === 1
                          ? "workspace_launcher.resume.restore_one"
                          : "workspace_launcher.resume.restore_many",
                      ),
                      { count: restorableWorkspaces },
                    )}
              </span>
              <RotateCcw className={cn("h-4 w-4", busy && "animate-spin")} />
            </Button>
            <Button
              variant="subtle"
              data-testid="resume-dismiss"
              disabled={busy}
              onClick={onDismiss}
              title={t("workspace_launcher.resume.discard_title")}
              className="h-9 w-full justify-between px-4"
            >
              <span>{t("workspace_launcher.resume.start_fresh")}</span>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>

      <dl className="mt-6 grid grid-cols-2 border-y border-border/70 sm:grid-cols-4 sm:divide-x sm:divide-border/70">
        {[
          [
            offer.workspace_count,
            t("workspace_launcher.resume.metric_workspaces"),
          ],
          [
            offer.terminal_count,
            t("workspace_launcher.resume.metric_terminals"),
          ],
          [
            offer.resumable_count,
            t("workspace_launcher.resume.metric_continuing"),
          ],
          [fresh, t("workspace_launcher.resume.metric_fresh")],
        ].map(([value, label], index) => (
          <div
            key={String(label)}
            className={cn(
              "py-3 sm:px-4",
              index % 2 === 1 && "pl-4 sm:pl-4",
              index >= 2 && "border-t border-border/70 sm:border-t-0",
              index === 0 && "sm:pl-0",
            )}
          >
            <dd className="font-mono text-lg font-semibold tabular-nums text-foreground">
              {value}
            </dd>
            <dt className="mt-0.5 text-[9px] uppercase tracking-[0.14em] text-muted-foreground">
              {label}
            </dt>
          </div>
        ))}
      </dl>

      {unavailable > 0 && (
        <p className="mt-3 flex items-center gap-2 border-l-2 border-amber-600/70 bg-amber-600/[0.07] px-3 py-2 text-xs text-amber-900 dark:border-amber-300/70 dark:bg-amber-300/[0.04] dark:text-amber-100">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {fill(t("workspace_launcher.resume.unavailable_warning"), {
            count: unavailable,
          })}
        </p>
      )}

      <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1fr)_270px]">
        <div className="min-w-0">
          <div className="flex items-end justify-between gap-3">
            <div>
              <SectionLabel>
                {t("workspace_launcher.resume.workspace_manifest")}
              </SectionLabel>
              <p className="mt-1 text-xs text-muted-foreground">
                {t("workspace_launcher.resume.workspace_manifest_hint")}
              </p>
            </div>
            <span className="hidden font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground sm:block">
              {fill(t("workspace_launcher.resume.available_workspaces"), {
                available: restorableWorkspaces,
                total: coming.length,
              })}
            </span>
          </div>
          <ul className="mt-3 border-b border-border/70">
            {coming.map((space, index) => (
              <WorkspaceRow
                key={space.session_id || `${space.folder}#${index}`}
                space={space}
                t={t}
              />
            ))}
          </ul>
          <EarlierWorkspaces spaces={earlier} t={t} />
        </div>

        <FleetManifest fleet={fleet} t={t} />
      </div>
    </section>
  );
}
