/**
 * "Inside right now" on a building card: what the building stands for, as
 * the app's own sections list it — real rows with the real marks, never a
 * name in a chip (maintainer, 2026-09-02: "like the Plugins section, with
 * the original logos").
 *
 *   Plugin Docks     → the marketplace plugins (BrandTile, the Plugins view's own tile)
 *   Terminal Cantina → the coding CLIs (CliLogo, the CLIs view's own mark)
 *   Relay Tower      → the MCP servers (a server glyph; servers carry no brand)
 *   Skill Forge      → the skills (a skill glyph)
 *
 * Every list is read from the endpoint its section reads, grouped the way
 * that section groups, with the shared StatusDot so "connected" looks the
 * same here as one section over. Nothing is invented: an empty answer says
 * so, a failed one shows the error.
 */
import { useQuery } from "@tanstack/react-query";
import { Server, Sparkles } from "lucide-react";
import type { ReactNode } from "react";

import { AgentMark } from "@/components/agentic/AgentMark";
import { CliLogo } from "@/components/clis/CliLogo";
import { StatusDot } from "@/components/extensions/primitives";
import type { CliStatus, CliSummary } from "@/hooks/useClis";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { fetchWorkspaceAgents } from "@/lib/workspaceApi";
import { BrandTile, type Plugin } from "@/views/PluginsView";

import type { BuildingPlace } from "./buildingCards";

type Tone = "ok" | "off" | "warn" | "error" | "busy";

interface Row {
  id: string;
  name: string;
  description: string;
  /** A second line under the name, when the description is missing: a version, a transport. */
  meta?: string;
  tone: Tone;
  /** `society.world.<key>` */
  statusKey: string;
  mark: ReactNode;
}

interface Group {
  /** `society.world.<key>` */
  labelKey: string;
  rows: Row[];
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// loaders — one per hub, each in its section's vocabulary
// ---------------------------------------------------------------------------

interface CatalogPlugin {
  id: string;
  display_name: string;
  description: string;
  category: string;
  logo_slug: string;
  logo_color?: string | null;
  logo_url?: string | null;
  status: string;
}

function pluginTone(status: string): { tone: Tone; statusKey: string } {
  if (status === "connected") return { tone: "ok", statusKey: "status_connected" };
  if (status === "needs_reauth" || status === "error") return { tone: "warn", statusKey: "status_attention" };
  return { tone: "off", statusKey: "status_available" };
}

async function loadPlugins(): Promise<Group[]> {
  const data = await getJson<{ plugins?: CatalogPlugin[] } | CatalogPlugin[]>("/api/marketplace/plugins");
  const list = Array.isArray(data) ? data : (data.plugins ?? []);
  const rows = list.map((p): Row => {
    const { tone, statusKey } = pluginTone(p.status);
    // BrandTile reads the same fields the Plugins view adapts from the catalog.
    const plugin = {
      id: p.id,
      name: p.display_name,
      description: p.description,
      logoSlug: p.logo_slug,
      logoColor: p.logo_color ?? undefined,
      logoUrl: p.logo_url ?? undefined,
      status: p.status,
    } as unknown as Plugin;
    return {
      id: p.id,
      name: p.display_name,
      description: p.description,
      meta: p.category,
      tone,
      statusKey,
      mark: <BrandTile plugin={plugin} size="sm" />,
    };
  });
  const byName = (a: Row, b: Row) => a.name.localeCompare(b.name);
  return [
    { labelKey: "plugin_group_connected", rows: rows.filter((r) => r.tone !== "off").sort(byName) },
    { labelKey: "plugin_group_available", rows: rows.filter((r) => r.tone === "off").sort(byName) },
  ];
}

const CLI_TONES: Record<CliStatus, { tone: Tone; statusKey: string }> = {
  connected: { tone: "ok", statusKey: "status_connected" },
  disconnected: { tone: "off", statusKey: "status_installed" },
  not_installed: { tone: "off", statusKey: "status_missing" },
  error: { tone: "error", statusKey: "status_attention" },
  checking: { tone: "busy", statusKey: "status_checking" },
};

/**
 * The Cantina seats two kinds: the coding agents an agent can run AS (the
 * Agentic IDE's registry — Claude Code, Codex, Antigravity…, each with its
 * own mark), then the tool CLIs the CLIs section lists.
 */
async function loadClis(): Promise<Group[]> {
  const [seats, data] = await Promise.all([
    fetchWorkspaceAgents().catch(() => ({ agents: [] })),
    getJson<{ clis: CliSummary[] }>("/api/clis"),
  ]);
  const byName = (a: Row, b: Row) => a.name.localeCompare(b.name);
  const seatRows = (seats.agents ?? [])
    .map((a): Row => ({
      id: `seat:${a.name}`,
      name: a.display_name || a.name,
      description: "",
      meta: a.version ? `v${a.version.replace(/^v/i, "")}` : undefined,
      tone: a.installed ? "ok" : "off",
      statusKey: a.installed ? "status_installed" : "status_missing",
      mark: <AgentMark agent={a.name} label={a.display_name || a.name} size="sm" />,
    }))
    .sort((a, b) => Number(b.tone === "ok") - Number(a.tone === "ok") || byName(a, b));
  const rows = (data.clis ?? []).map((c): Row => ({
    id: c.name,
    name: c.display_name || c.name,
    description: c.description,
    meta: c.version ? `v${c.version.replace(/^v/i, "")}` : undefined,
    ...CLI_TONES[c.status],
    mark: <CliLogo cliName={c.name} category={c.category} size="sm" />,
  }));
  return [
    { labelKey: "cli_group_seats", rows: seatRows },
    { labelKey: "cli_group_connected", rows: rows.filter((r) => r.statusKey === "status_connected").sort(byName) },
    { labelKey: "cli_group_installed", rows: rows.filter((r) => r.statusKey === "status_installed" || r.tone === "error" || r.tone === "busy").sort(byName) },
    { labelKey: "cli_group_missing", rows: rows.filter((r) => r.statusKey === "status_missing").sort(byName) },
  ];
}

interface McpServer {
  name: string;
  display?: string;
  description?: string;
  transport?: string;
  status?: string;
  enabled?: boolean;
  tools?: unknown[];
}

function GlyphTile({ children }: { children: ReactNode }) {
  return (
    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-border/60 bg-secondary text-muted-foreground">
      {children}
    </span>
  );
}

async function loadMcps(): Promise<Group[]> {
  const data = await getJson<{ servers: McpServer[] }>("/api/mcps");
  const rows = (data.servers ?? []).map((s): Row => {
    const running = s.status === "running";
    const tools = Array.isArray(s.tools) ? s.tools.length : 0;
    return {
      id: s.name,
      name: s.display || s.name,
      description: s.description ?? "",
      meta: [s.transport ?? "stdio", tools ? `${tools} tools` : null].filter(Boolean).join(" · "),
      tone: running ? "ok" : s.enabled === false ? "off" : "off",
      statusKey: running ? "status_running" : "status_stopped",
      mark: (
        <GlyphTile>
          <Server className="h-3.5 w-3.5" aria-hidden />
        </GlyphTile>
      ),
    };
  });
  const byName = (a: Row, b: Row) => a.name.localeCompare(b.name);
  return [
    { labelKey: "mcp_group_stdio", rows: rows.filter((r) => !r.meta?.startsWith("http")).sort(byName) },
    { labelKey: "mcp_group_http", rows: rows.filter((r) => r.meta?.startsWith("http")).sort(byName) },
  ];
}

interface SkillRow {
  name: string;
  description?: string;
  state: string;
  is_builtin: boolean;
}

async function loadSkills(): Promise<Group[]> {
  const data = await getJson<{ skills: SkillRow[] }>("/api/skills");
  const rows = (data.skills ?? []).map((s): Row => ({
    id: s.name,
    name: s.name,
    description: s.description ?? "",
    tone: s.state === "draft" ? "warn" : "ok",
    statusKey: s.state === "draft" ? "status_draft" : "status_ready",
    mark: (
      <GlyphTile>
        <Sparkles className="h-3.5 w-3.5" aria-hidden />
      </GlyphTile>
    ),
    meta: s.is_builtin ? undefined : undefined,
  }));
  const byName = (a: Row, b: Row) => a.name.localeCompare(b.name);
  const skills = data.skills ?? [];
  const state = (name: string) => skills.find((s) => s.name === name);
  return [
    { labelKey: "skills_group_draft", rows: rows.filter((r) => state(r.id)?.state === "draft").sort(byName) },
    {
      labelKey: "skills_group_custom",
      rows: rows.filter((r) => state(r.id)?.state !== "draft" && !state(r.id)?.is_builtin).sort(byName),
    },
    {
      labelKey: "skills_group_builtin",
      rows: rows.filter((r) => state(r.id)?.state !== "draft" && state(r.id)?.is_builtin).sort(byName),
    },
  ];
}

const LOADERS: Partial<Record<BuildingPlace, () => Promise<Group[]>>> = {
  plugins: loadPlugins,
  cli: loadClis,
  mcp: loadMcps,
  skills: loadSkills,
};

/** Whether this building lists anything (the Foundry and the Memory House explain themselves). */
export function hasInside(place: BuildingPlace): boolean {
  return place in LOADERS;
}

// ---------------------------------------------------------------------------
// the list
// ---------------------------------------------------------------------------

export function BuildingInside({ place, className }: { place: BuildingPlace; className?: string }) {
  const t = useT();
  const load = LOADERS[place];
  const inside = useQuery({
    queryKey: ["society", "building-inside", place],
    queryFn: load ?? (async () => []),
    enabled: Boolean(load),
    staleTime: 60_000,
  });
  if (!load) return null;
  const groups = (inside.data ?? []).filter((g) => g.rows.length > 0);
  const total = groups.reduce((n, g) => n + g.rows.length, 0);

  return (
    <div className={cn("flex flex-col gap-4", className)} data-testid="building-card-inside">
      {inside.isLoading ? (
        <p className="text-sm text-muted-foreground">{t("society.world.drawer_loading")}</p>
      ) : inside.isError ? (
        <p className="text-sm text-destructive">{String(inside.error)}</p>
      ) : total === 0 ? (
        <p className="text-sm text-muted-foreground">{t("society.world.drawer_empty")}</p>
      ) : (
        groups.map((g) => (
          <section key={g.labelKey}>
            <h4 className="mb-1 flex items-center gap-2 px-2 text-xs font-medium text-muted-foreground">
              {t(`society.world.${g.labelKey}`)}
              <span className="tabular-nums">{g.rows.length}</span>
            </h4>
            <ul className="flex flex-col">
              {g.rows.map((r) => (
                <li key={r.id} className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-secondary/60">
                  {r.mark}
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="truncate text-sm font-medium text-foreground">{r.name}</span>
                      {r.meta ? <span className="shrink-0 font-mono text-xs text-muted-foreground">{r.meta}</span> : null}
                    </div>
                    {r.description ? (
                      <p className="truncate text-xs text-muted-foreground" title={r.description}>
                        {r.description}
                      </p>
                    ) : null}
                  </div>
                  <span className="shrink-0 text-xs">
                    <StatusDot tone={r.tone} label={t(`society.world.${r.statusKey}`)} pulse={r.tone === "busy"} />
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}

export default BuildingInside;
