import { useMemo, useState, useEffect, useCallback } from "react";
import {
  Puzzle,
  RefreshCw,
  Lock,
  AlertTriangle,
  Mic,
  Keyboard,
  Clock,
  Save,
  PowerOff,
  Power,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  FileText,
  FileCode,
  FileBox,
  UserSquare,
  X,
  Sparkles,
  Plus,
  Search,
  X as XIcon,
  Sparkle,
  ExternalLink,
  Home,
  BookOpen,
  Github,
} from "lucide-react";
import { SkillFinderDialog } from "@/views/SkillFinderDialog";
import { SkillCreateDialog } from "@/views/SkillCreateDialog";
import { ViewHeader } from "@/views/ChatsView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  useSkillsList,
  useSkillDetail,
  useSaveSkill,
  useSetSkillEnabled,
  useReloadSkills,
  useSkillResource,
  useLocalSkillSearch,
  useSkillLinkHealth,
  RESOURCE_KINDS,
  RESOURCE_LABELS,
  type SkillSummary,
  type SkillState,
  type SkillTrigger,
  type ResourceKind,
  type LocalSkillHit,
  type LocalSkillQueryFilters,
  type LinkHealthEntry,
} from "@/hooks/useSkills";

const STATE_LABEL: Record<SkillState, string> = {
  active: "aktiv",
  validated: "bereit",
  draft: "fehler",
  disabled: "aus",
};

const STATE_VARIANT: Record<
  SkillState,
  "default" | "secondary" | "destructive" | "outline"
> = {
  active: "default",
  validated: "secondary",
  draft: "destructive",
  disabled: "outline",
};

const TRIGGER_ICON: Record<SkillTrigger["type"], typeof Mic> = {
  voice: Mic,
  hotkey: Keyboard,
  schedule: Clock,
};

// In-memory Admin-Pass — haelt den Pass fuer die Session, damit der User ihn
// nicht bei jedem Edit neu eingibt. Absichtlich kein localStorage: wer die
// App schliesst, gibt den Pass beim naechsten Start neu ein.
let sessionAdminPass: string | null = null;

export function SkillsView() {
  const t = useT();
  const { data, isLoading, error, refetch, isRefetching } = useSkillsList();
  const reload = useReloadSkills();
  const [selected, setSelected] = useState<string | null>(null);
  const [finderOpen, setFinderOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const [queryInput, setQueryInput] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [ownerFilter, setOwnerFilter] = useState<"all" | "user" | "builtin">("all");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  // Debounce: 250ms nach letztem Tastendruck
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(queryInput.trim()), 250);
    return () => clearTimeout(t);
  }, [queryInput]);

  const searchActive =
    debouncedQuery.length > 0 ||
    ownerFilter !== "all" ||
    categoryFilter !== null;

  const searchFilters: LocalSkillQueryFilters = useMemo(
    () => ({
      q: debouncedQuery,
      category: categoryFilter,
      is_builtin:
        ownerFilter === "user" ? false : ownerFilter === "builtin" ? true : null,
      limit: 50,
    }),
    [debouncedQuery, categoryFilter, ownerFilter],
  );

  const search = useLocalSkillSearch(searchFilters, searchActive);

  const grouped = useMemo(() => groupByCategory(data?.skills ?? []), [data]);

  const categoryOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const s of data?.skills ?? []) {
      if (s.category) seen.add(s.category);
    }
    return Array.from(seen).sort();
  }, [data]);

  const handleRefresh = () => {
    reload.mutate();
    void refetch();
  };

  const clearFilters = () => {
    setQueryInput("");
    setDebouncedQuery("");
    setOwnerFilter("all");
    setCategoryFilter(null);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Puzzle className="h-4 w-4 text-primary" />}
        title={t("skills_view.title")}
        subtitle={t("skills_view.subtitle")}
        right={
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant="default"
              onClick={() => setCreateOpen(true)}
              className="gap-1.5"
            >
              <Plus className="h-3.5 w-3.5" />
              Neuer Skill
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setFinderOpen(true)}
              className="gap-1.5"
            >
              <Sparkles className="h-3.5 w-3.5" />
              Skill suchen
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleRefresh}
              disabled={isRefetching || reload.isPending}
            >
              <RefreshCw
                className={cn(
                  "h-4 w-4",
                  (isRefetching || reload.isPending) && "animate-spin",
                )}
              />
            </Button>
          </div>
        }
      />

      <SkillFinderDialog
        open={finderOpen}
        onClose={() => setFinderOpen(false)}
      />

      <SkillCreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(name) => setSelected(name)}
      />

      <div className="flex min-h-0 flex-1">
        {/* Linke Spalte: Liste */}
        <div className="flex w-[340px] flex-col border-r border-border">
          {/* Suchleiste + Filter-Chips */}
          <div className="space-y-2 border-b border-border bg-muted/20 px-3 py-2.5">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <input
                type="text"
                value={queryInput}
                onChange={(e) => setQueryInput(e.target.value)}
                placeholder="Skills durchsuchen…"
                className="w-full rounded-md border border-border bg-background py-1.5 pl-7 pr-7 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
              />
              {queryInput && (
                <button
                  type="button"
                  onClick={() => setQueryInput("")}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  aria-label="Suche leeren"
                >
                  <XIcon className="h-3 w-3" />
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1">
              <FilterChip
                label="Alle"
                active={ownerFilter === "all"}
                onClick={() => setOwnerFilter("all")}
              />
              <FilterChip
                label="Meine"
                active={ownerFilter === "user"}
                onClick={() => setOwnerFilter("user")}
              />
              <FilterChip
                label="Builtin"
                active={ownerFilter === "builtin"}
                onClick={() => setOwnerFilter("builtin")}
              />
              {categoryOptions.length > 0 && (
                <select
                  value={categoryFilter ?? ""}
                  onChange={(e) => setCategoryFilter(e.target.value || null)}
                  className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">Alle Kategorien</option>
                  {categoryOptions.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
              {searchActive && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="ml-auto text-[10px] text-muted-foreground underline hover:text-foreground"
                >
                  Zuruecksetzen
                </button>
              )}
            </div>
            {searchActive && search.data && (
              <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                <span>{search.data.total} Treffer</span>
                {search.data.brain_used && (
                  <span className="flex items-center gap-1">
                    <Sparkle className="h-2.5 w-2.5" />
                    KI-geranked
                  </span>
                )}
              </div>
            )}
          </div>
          <ScrollArea className="flex-1">
            <div className="space-y-6 p-4">
              {isLoading && (
                <div className="text-sm text-muted-foreground">Lade Skills…</div>
              )}
              {error && (
                <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
                  Konnte Skills nicht laden: {(error as Error).message}
                </div>
              )}
              {searchActive ? (
                <SearchResults
                  isPending={search.isPending || search.isFetching}
                  error={search.error as Error | null}
                  results={search.data?.skills ?? []}
                  selected={selected}
                  onSelect={setSelected}
                />
              ) : (
                <>
                  {!isLoading && !error && grouped.length === 0 && <EmptyList />}
                  {grouped.map(([category, skills]) => (
                    <section key={category}>
                      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                        {category}
                      </h3>
                      <ul className="space-y-1.5">
                        {skills.map((s) => (
                          <SkillRow
                            key={s.name}
                            skill={s}
                            selected={selected === s.name}
                            onClick={() => setSelected(s.name)}
                          />
                        ))}
                      </ul>
                    </section>
                  ))}
                </>
              )}
            </div>
          </ScrollArea>
        </div>

        {/* Rechte Spalte: Detail-Panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          {selected ? (
            <SkillDetailPanel name={selected} />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              Waehle einen Skill aus der Liste.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Linke Spalte — List Row
// ----------------------------------------------------------------------

function SkillRow({
  skill,
  selected,
  onClick,
}: {
  skill: SkillSummary;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "w-full rounded-md border p-2.5 text-left transition-colors",
          selected
            ? "border-primary/60 bg-primary/5"
            : "border-border hover:bg-muted/40",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{skill.name}</span>
              {skill.is_builtin && (
                <Lock
                  className="h-3 w-3 flex-shrink-0 text-muted-foreground"
                  aria-label="Builtin"
                />
              )}
              {skill.state === "draft" && (
                <AlertTriangle
                  className="h-3.5 w-3.5 flex-shrink-0 text-destructive"
                  aria-label="Fehler"
                />
              )}
            </div>
            {skill.description && (
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {skill.description}
              </p>
            )}
            <div className="mt-2 flex items-center gap-1.5">
              {skill.triggers.map((t, i) => {
                const Icon = TRIGGER_ICON[t.type];
                return (
                  <Icon
                    key={i}
                    className="h-3 w-3 text-muted-foreground"
                    aria-label={t.type}
                  />
                );
              })}
              {skill.triggers.length === 0 && (
                <span className="text-[10px] text-muted-foreground">
                  kein auto-trigger
                </span>
              )}
            </div>
          </div>
          <Badge variant={STATE_VARIANT[skill.state]} className="flex-shrink-0">
            {STATE_LABEL[skill.state]}
          </Badge>
        </div>
      </button>
    </li>
  );
}

// ----------------------------------------------------------------------
// Rechte Spalte — Detail-Panel
// ----------------------------------------------------------------------

function SkillDetailPanel({ name }: { name: string }) {
  const { data, isLoading, error, refetch } = useSkillDetail(name);
  const save = useSaveSkill();
  const setEnabled = useSetSkillEnabled();

  // Link-Health nur laden, wenn das Frontmatter tatsaechlich URLs enthaelt —
  // sonst wuerde der Endpoint fuer jedes Skill-Opening feuern, was vor allem
  // bei grossen Skill-Listen unnoetigen HEAD-Traffic produziert.
  const hasLinks = useMemo(() => {
    const fm = data?.frontmatter as Record<string, unknown> | null | undefined;
    if (!fm) return false;
    return Boolean(fm.homepage_url || fm.source_url || fm.docs_url);
  }, [data]);
  const linkHealth = useSkillLinkHealth(name, hasLinks);

  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  const [showAdminDialog, setShowAdminDialog] = useState(false);
  const [adminPassInput, setAdminPassInput] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [openResource, setOpenResource] = useState<{
    kind: ResourceKind;
    filename: string;
  } | null>(null);

  useEffect(() => {
    // Reset den Draft + Resource-Viewer, wenn das geladene Skill wechselt
    if (data) {
      setDraft(buildSkillMdText(data));
      setDirty(false);
      setSaveError(null);
      setOpenResource(null);
    }
  }, [data]);

  const handleSave = useCallback(
    async (adminPass?: string) => {
      if (!data) return;
      setSaveError(null);
      try {
        await save.mutateAsync({
          name: data.name,
          content: draft,
          adminPassword: adminPass ?? sessionAdminPass ?? undefined,
        });
        setDirty(false);
        setShowAdminDialog(false);
      } catch (e) {
        const msg = (e as Error).message;
        setSaveError(msg);
        // 403 -> Admin-Pass noetig
        if (
          data.is_builtin &&
          (msg.includes("Admin-Password") || msg.includes("403"))
        ) {
          sessionAdminPass = null;
          setShowAdminDialog(true);
        }
      }
    },
    [data, draft, save],
  );

  const handleSaveClick = () => {
    if (!data) return;
    if (data.is_builtin && !sessionAdminPass) {
      setShowAdminDialog(true);
      return;
    }
    void handleSave();
  };

  if (isLoading || !data) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Lade Skill…
      </div>
    );
  }
  if (error) {
    return (
      <div className="p-6 text-sm text-destructive">
        Fehler: {(error as Error).message}
      </div>
    );
  }

  const isActive = data.state === "active";
  const isDraft = data.state === "draft";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-border px-6 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="truncate text-lg font-semibold">{data.name}</h2>
              <Badge variant={STATE_VARIANT[data.state]}>
                {STATE_LABEL[data.state]}
              </Badge>
              {data.is_builtin && (
                <Badge variant="outline" className="gap-1">
                  <Lock className="h-3 w-3" />
                  builtin
                </Badge>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              v{data.version} · {data.category} · {data.path}
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={isDraft || setEnabled.isPending}
              onClick={() =>
                setEnabled.mutate(
                  { name: data.name, enabled: !isActive },
                  { onSuccess: () => refetch() },
                )
              }
            >
              {isActive ? (
                <>
                  <PowerOff className="mr-1.5 h-3.5 w-3.5" />
                  Deaktivieren
                </>
              ) : (
                <>
                  <Power className="mr-1.5 h-3.5 w-3.5" />
                  Aktivieren
                </>
              )}
            </Button>
            <Button
              size="sm"
              onClick={handleSaveClick}
              disabled={!dirty || save.isPending}
            >
              <Save className="mr-1.5 h-3.5 w-3.5" />
              {save.isPending ? "Speichert…" : "Speichern"}
            </Button>
          </div>
        </div>

        {data.error && (
          <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            Validation-Fehler: {data.error}
          </div>
        )}
        {saveError && !showAdminDialog && (
          <div className="mt-3 rounded-md border border-destructive/40 bg-destructive/10 p-2.5 text-xs text-destructive">
            {saveError}
          </div>
        )}
        {hasLinks && (
          <SkillLinks
            frontmatter={data.frontmatter as Record<string, unknown> | null}
            health={linkHealth.data?.fields ?? null}
          />
        )}
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Linke Sub-Spalte: Editor */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-border px-6 py-2">
            <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
              SKILL.md
            </span>
            {data.is_builtin && (
              <span className="text-[10px] text-muted-foreground">
                Builtin — Admin-Password zum Speichern noetig
              </span>
            )}
          </div>
          <textarea
            className={cn(
              "flex-1 resize-none bg-background p-6 font-mono text-xs",
              "focus:outline-none",
            )}
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setDirty(e.target.value !== buildSkillMdText(data));
            }}
            spellCheck={false}
          />
        </div>

        {/* Rechte Sub-Spalte: Bundle-Tree + optional Resource-Viewer */}
        {data.resource_count > 0 && (
          <div className="flex w-[320px] flex-col border-l border-border">
            {openResource ? (
              <ResourceViewer
                skillName={data.name}
                kind={openResource.kind}
                filename={openResource.filename}
                onClose={() => setOpenResource(null)}
              />
            ) : (
              <ResourceTree
                resources={data.resources}
                onOpen={(kind, filename) => setOpenResource({ kind, filename })}
              />
            )}
          </div>
        )}
      </div>

      {showAdminDialog && (
        <AdminPassDialog
          onConfirm={(pass) => {
            sessionAdminPass = pass;
            setAdminPassInput("");
            void handleSave(pass);
          }}
          onCancel={() => {
            setShowAdminDialog(false);
            setAdminPassInput("");
          }}
          passInput={adminPassInput}
          setPassInput={setAdminPassInput}
          errorHint={saveError}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Admin-Pass-Dialog
// ----------------------------------------------------------------------

function AdminPassDialog({
  onConfirm,
  onCancel,
  passInput,
  setPassInput,
  errorHint,
}: {
  onConfirm: (pass: string) => void;
  onCancel: () => void;
  passInput: string;
  setPassInput: (v: string) => void;
  errorHint: string | null;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[400px] rounded-lg border border-border bg-card p-6 shadow-xl">
        <h3 className="flex items-center gap-2 text-base font-semibold">
          <Lock className="h-4 w-4" />
          Admin-Password
        </h3>
        <p className="mt-2 text-sm text-muted-foreground">
          Builtin-Skills sind geschuetzt. Gib das in <code>jarvis.toml</code> unter
          <code> [security]</code> gesetzte Admin-Password ein.
        </p>
        <input
          type="password"
          autoFocus
          value={passInput}
          onChange={(e) => setPassInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && passInput) onConfirm(passInput);
            if (e.key === "Escape") onCancel();
          }}
          className="mt-4 w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="Password"
        />
        {errorHint && (
          <p className="mt-2 text-xs text-destructive">{errorHint}</p>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Abbrechen
          </Button>
          <Button
            size="sm"
            disabled={!passInput}
            onClick={() => onConfirm(passInput)}
          >
            Entsperren
          </Button>
        </div>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------

function groupByCategory(
  skills: SkillSummary[],
): [string, SkillSummary[]][] {
  const groups = new Map<string, SkillSummary[]>();
  for (const s of skills) {
    const key = s.category || "general";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(s);
  }
  for (const list of groups.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
}

/**
 * Baut die SKILL.md-Textrepraesentation aus Frontmatter + Body. Die PUT-Route
 * erwartet das vollstaendige File — wir rekonstruieren es hier clientseitig,
 * statt das Backend einen separaten "nur-body"-Endpoint bieten zu lassen.
 */
function buildSkillMdText(
  data: { body: string; frontmatter: Record<string, unknown> | null },
): string {
  if (!data.frontmatter) return data.body;
  const fmYaml = serializeFrontmatter(data.frontmatter);
  return `---\n${fmYaml}---\n\n${data.body}`;
}

function serializeFrontmatter(fm: Record<string, unknown>): string {
  // Minimaler YAML-Dumper — keine komplexen Cases (Refs, Ankers). Wir wissen,
  // dass SkillFrontmatter nur primitive + Listen + flache Dicts enthaelt.
  const lines: string[] = [];
  const dump = (key: string, val: unknown, indent = 0): void => {
    const pad = " ".repeat(indent);
    if (val === null || val === undefined) {
      lines.push(`${pad}${key}: null`);
      return;
    }
    if (Array.isArray(val)) {
      if (val.length === 0) {
        lines.push(`${pad}${key}: []`);
        return;
      }
      lines.push(`${pad}${key}:`);
      for (const item of val) {
        if (typeof item === "object" && item !== null) {
          const entries = Object.entries(item as Record<string, unknown>);
          const [firstKey, firstVal] = entries[0];
          lines.push(`${pad}  - ${firstKey}: ${formatScalar(firstVal)}`);
          for (const [k, v] of entries.slice(1)) {
            lines.push(`${pad}    ${k}: ${formatScalar(v)}`);
          }
        } else {
          lines.push(`${pad}  - ${formatScalar(item)}`);
        }
      }
      return;
    }
    if (typeof val === "object") {
      lines.push(`${pad}${key}:`);
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        dump(k, v, indent + 2);
      }
      return;
    }
    lines.push(`${pad}${key}: ${formatScalar(val)}`);
  };

  for (const [k, v] of Object.entries(fm)) {
    dump(k, v);
  }
  return lines.join("\n") + "\n";
}

function formatScalar(val: unknown): string {
  if (val === null || val === undefined) return "null";
  if (typeof val === "boolean" || typeof val === "number") return String(val);
  const str = String(val);
  if (/[:#\n]/.test(str) || str.trim() !== str || str === "") {
    // Mit Doppelpunkten, Hashes oder Leerzeichen-Paddding → quoten
    return JSON.stringify(str);
  }
  return str;
}

// ----------------------------------------------------------------------
// Bundle-Resource-Tree + Viewer
// ----------------------------------------------------------------------

const KIND_ICON: Record<ResourceKind, typeof FileText> = {
  references: FileText,
  scripts: FileCode,
  assets: FileBox,
  agents: UserSquare,
};

function ResourceTree({
  resources,
  onOpen,
}: {
  resources: Record<ResourceKind, string[]>;
  onOpen: (kind: ResourceKind, filename: string) => void;
}) {
  const [expanded, setExpanded] = useState<Record<ResourceKind, boolean>>({
    references: true,
    scripts: false,
    assets: false,
    agents: false,
  });

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
          Bundle
        </span>
      </div>
      <ScrollArea className="flex-1">
        <div className="px-3 py-2">
          {RESOURCE_KINDS.map((kind) => {
            const files = resources[kind];
            if (!files || files.length === 0) return null;
            const isOpen = expanded[kind];
            const Icon = KIND_ICON[kind];
            return (
              <div key={kind} className="mb-1">
                <button
                  type="button"
                  onClick={() =>
                    setExpanded((p) => ({ ...p, [kind]: !p[kind] }))
                  }
                  className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-xs hover:bg-muted/50"
                >
                  {isOpen ? (
                    <ChevronDown className="h-3 w-3 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-3 w-3 text-muted-foreground" />
                  )}
                  <Icon className="h-3 w-3 text-muted-foreground" />
                  <span className="font-medium">
                    {RESOURCE_LABELS[kind]}
                  </span>
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {files.length}
                  </span>
                </button>
                {isOpen && (
                  <ul className="mt-0.5 ml-5 space-y-0.5">
                    {files.map((f) => (
                      <li key={f}>
                        <button
                          type="button"
                          onClick={() => onOpen(kind, f)}
                          className="w-full truncate rounded px-2 py-0.5 text-left text-[11px] font-mono hover:bg-muted/50"
                        >
                          {f}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </>
  );
}

function ResourceViewer({
  skillName,
  kind,
  filename,
  onClose,
}: {
  skillName: string;
  kind: ResourceKind;
  filename: string;
  onClose: () => void;
}) {
  const t = useT();
  const { data, isLoading, error } = useSkillResource(skillName, kind, filename);
  const Icon = KIND_ICON[kind];

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Icon className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
        <span className="truncate text-xs font-mono" title={`${kind}/${filename}`}>
          {kind}/{filename}
        </span>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto h-6 w-6 p-0"
          onClick={onClose}
          title={t("skills_toast.back_to_list")}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {isLoading && (
        <div className="p-4 text-xs text-muted-foreground">Laedt…</div>
      )}
      {error && (
        <div className="p-4 text-xs text-destructive">
          {(error as Error).message}
        </div>
      )}
      {data && (
        <ScrollArea className="flex-1">
          <pre className="whitespace-pre-wrap break-words p-4 font-mono text-[11px] leading-relaxed">
            {data}
          </pre>
        </ScrollArea>
      )}
    </>
  );
}

function EmptyList() {
  return (
    <div className="space-y-3 text-sm text-muted-foreground">
      <p>Noch keine Skills da.</p>
      <p className="text-xs">
        Beim ersten Start kopiert Jarvis die Builtin-Skills nach
        <br />
        <code>%LOCALAPPDATA%\Jarvis\skills</code>. Passiert das nicht, check
        die Backend-Logs.
      </p>
    </div>
  );
}

// ----------------------------------------------------------------------
// Skill-Links (homepage / source / docs) mit Health-Chip
// ----------------------------------------------------------------------

interface LinkFieldSpec {
  key: "homepage_url" | "source_url" | "docs_url";
  label: string;
  icon: typeof Home;
}

const LINK_FIELDS: LinkFieldSpec[] = [
  { key: "homepage_url", label: "Homepage", icon: Home },
  { key: "source_url", label: "Source", icon: Github },
  { key: "docs_url", label: "Docs", icon: BookOpen },
];

function SkillLinks({
  frontmatter,
  health,
}: {
  frontmatter: Record<string, unknown> | null;
  health: Partial<Record<"homepage_url" | "source_url" | "docs_url", LinkHealthEntry | null>> | null;
}) {
  if (!frontmatter) return null;
  const visible = LINK_FIELDS.filter((f) => Boolean(frontmatter[f.key]));
  if (visible.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3">
      {visible.map((f) => {
        const url = String(frontmatter[f.key]);
        const h = health?.[f.key] ?? null;
        const Icon = f.icon;
        return (
          <a
            key={f.key}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground hover:bg-muted/40"
            title={url}
          >
            <Icon className="h-3 w-3 text-muted-foreground" />
            <span className="max-w-[180px] truncate">{f.label}</span>
            <LinkHealthChip entry={h} />
            <ExternalLink className="h-3 w-3 text-muted-foreground opacity-0 group-hover:opacity-100" />
          </a>
        );
      })}
    </div>
  );
}

function LinkHealthChip({ entry }: { entry: LinkHealthEntry | null }) {
  const t = useT();
  const { color, label } = useMemo(() => {
    if (!entry) return { color: "bg-muted-foreground/40", label: "not checked" };
    if (entry.status === 0) return { color: "bg-destructive", label: "no network" };
    if (entry.ok) return { color: "bg-emerald-500", label: `HTTP ${entry.status}` };
    return { color: "bg-destructive", label: `HTTP ${entry.status}` };
  }, [entry]);
  const stale = entry && !entry.fresh;
  return (
    <span
      className={cn(
        "h-2 w-2 rounded-full",
        color,
        stale && "animate-pulse opacity-60",
      )}
      title={`${label}${stale ? t("skills_toast.stale_refreshing") : ""}`}
      aria-label={label}
    />
  );
}

// ----------------------------------------------------------------------
// Search-Ergebnis-Liste (flach, mit Score-Reason)
// ----------------------------------------------------------------------

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] transition-colors",
        active
          ? "border-primary/60 bg-primary/10 text-primary"
          : "border-border bg-background hover:bg-muted/40",
      )}
    >
      {label}
    </button>
  );
}

function SearchResults({
  isPending,
  error,
  results,
  selected,
  onSelect,
}: {
  isPending: boolean;
  error: Error | null;
  results: LocalSkillHit[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  if (isPending && results.length === 0) {
    return <div className="text-xs text-muted-foreground">Searching…</div>;
  }
  if (error) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
        Search failed: {error.message}
      </div>
    );
  }
  if (results.length === 0) {
    return (
      <div className="rounded-lg border border-border p-3 text-xs text-muted-foreground">
        Keine Treffer. Probier einen anderen Begriff oder entferne einen Filter.
      </div>
    );
  }
  return (
    <ul className="space-y-1.5">
      {results.map((s) => (
        <SkillRowWithScore
          key={s.name}
          skill={s}
          selected={selected === s.name}
          onClick={() => onSelect(s.name)}
        />
      ))}
    </ul>
  );
}

function SkillRowWithScore({
  skill,
  selected,
  onClick,
}: {
  skill: LocalSkillHit;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "w-full rounded-md border p-2.5 text-left transition-colors",
          selected
            ? "border-primary/60 bg-primary/5"
            : "border-border hover:bg-muted/40",
        )}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">{skill.name}</span>
              {skill.is_builtin && (
                <Lock
                  className="h-3 w-3 flex-shrink-0 text-muted-foreground"
                  aria-label="Builtin"
                />
              )}
            </div>
            {skill.description && (
              <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                {skill.description}
              </p>
            )}
            {skill.reason && (
              <p className="mt-1 truncate text-[10px] italic text-muted-foreground/70">
                {skill.reason}
              </p>
            )}
          </div>
          <Badge variant="outline" className="flex-shrink-0 text-[10px]">
            {Math.round(skill.score * 100)}
          </Badge>
        </div>
      </button>
    </li>
  );
}
