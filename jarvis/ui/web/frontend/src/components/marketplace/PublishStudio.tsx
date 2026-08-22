import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ExternalLink,
  FileText,
  FolderUp,
  Github,
  Image as ImageIcon,
  Loader2,
  PencilLine,
  Plug,
  Search,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { InstallStandard, type InstallStandardWire } from "@/components/InstallStandard";
import {
  PublishIdentityCard,
  PublisherAvatar,
  usePublishIdentity,
} from "@/components/marketplace/PublishIdentity";
import { PublishWallpaperDialog } from "@/components/wallpaper/PublishWallpaperDialog";
import { thumbUrlFor, type WallpaperEntry } from "@/hooks/useWallpaperCatalog";
import { uploadAsEntry, useWallpaperUploads } from "@/hooks/useWallpaperUploads";
import { fill, useLocaleChunk, useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import {
  collectDroppedFiles,
  collectPickedFiles,
  folderToDraft,
  MAX_SKILL_MD_BYTES,
  type FolderDraft,
  type PackageSkill,
} from "@/lib/packageFolder";
import {
  fetchRepoFile,
  getRepo,
  listRepos,
  parseRepoRef,
  scanRepo,
  type RepoCandidate,
  type RepoSummary,
} from "@/lib/githubImport";

// ---------------------------------------------------------------------------
// The Publish Studio — the in-app half of the marketplace's publish path.
//
// A full-height overlay over the storefront with a rail of four stations on
// the left (who · what · check · live) and the work on the right. The author
// never starts at an empty form: the entry screen offers the ways a package
// can arrive — drop the folder, import it from a public GitHub repo, write it
// by hand, or pick one of your own wallpapers — and "the folder is the
// classification" (publishing-plan.md §2): what is inside decides whether it
// becomes a skill or a plugin card.
//
// Anyone may publish: whatever passes the automated checks goes live with no
// human review, so the copy stays honest about the two things that matter —
// the upload appears PUBLICLY under the user's GitHub name, and the only gate
// is the rule check. Identity comes from the GitHub device flow; the
// submission travels through the same storefront endpoint the web form uses,
// so web and app cannot drift apart.
// ---------------------------------------------------------------------------

type Translate = (key: string) => string;

interface FieldErrorWire {
  field: string | null;
  error: string;
}

interface SubmitResultWire {
  ok: boolean;
  name: string;
  version: string;
  pr_url?: string | null;
  submission_path?: string | null;
  /** The three commands other people will run to install this — computed
   *  server-side by install_standard.py, never derived here. */
  install?: InstallStandardWire | null;
}

type Kind = "skill" | "plugin";
type Stage = "source" | "github" | "wallpapers" | "form" | "done";

interface Draft {
  kind: Kind;
  name: string;
  version: string;
  title: string;
  description: string;
  categories: string;
  skill_md: string;
  plugin_json_text: string;
  mcp_json_text: string;
  usage_card: string;
  skills: PackageSkill[];
}

const EMPTY_DRAFT: Draft = {
  kind: "skill",
  name: "",
  version: "1.0.0",
  title: "",
  description: "",
  categories: "",
  skill_md: "",
  plugin_json_text: "",
  mcp_json_text: "",
  usage_card: "",
  skills: [],
};

const SKILL_TEMPLATE = `---
name: my-skill
description: One sentence on when the assistant should reach for this skill.
version: 1.0.0
---

# My Skill

Write the instructions the assistant follows, step by step.
`;

const PLUGIN_TEMPLATE = `{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "One sentence on what the plugin connects.",
  "extensions": {
    "io.github.personaljarvis": {
      "display_name": "My Plugin",
      "category": "Productivity",
      "auth": { "mode": "pat_paste", "pat_help_url": "https://example.com/settings/tokens" }
    }
  }
}
`;

const MCP_TEMPLATE = `{
  "mcpServers": {
    "my-plugin": { "type": "streamable-http", "url": "https://mcp.example.com/mcp" }
  }
}
`;

/** The JSON body the backend's validate/submit routes expect, or a field
 *  error when a pasted JSON block does not parse. */
function draftToBody(
  draft: Draft,
  t: Translate,
): { body: Record<string, unknown> } | { parseError: FieldErrorWire } {
  const base = {
    kind: draft.kind,
    name: draft.name.trim(),
    version: draft.version.trim(),
  };
  if (draft.kind === "skill") {
    return {
      body: {
        ...base,
        title: draft.title.trim(),
        description: draft.description.trim(),
        categories: draft.categories
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean),
        skill_md: draft.skill_md,
      },
    };
  }
  let pluginJson: unknown;
  try {
    pluginJson = draft.plugin_json_text.trim() ? JSON.parse(draft.plugin_json_text) : null;
  } catch {
    return {
      parseError: { field: "plugin_json", error: t("marketplace.studio_err_plugin_json") },
    };
  }
  let mcpJson: unknown = null;
  try {
    mcpJson = draft.mcp_json_text.trim() ? JSON.parse(draft.mcp_json_text) : null;
  } catch {
    return { parseError: { field: "mcp_json", error: t("marketplace.studio_err_mcp_json") } };
  }
  const body: Record<string, unknown> = {
    ...base,
    plugin_json: pluginJson,
    mcp_json: mcpJson,
    usage_card: draft.usage_card.trim() ? draft.usage_card : null,
  };
  // Bundled skills ride inside the plugin submission (registry schema W2).
  if (draft.skills.length > 0) body.skills = draft.skills;
  return { body };
}

/**
 * The studio. `initialStage` lets a caller open it straight at a lane — the
 * wallpaper picker's "Share" lands in the wallpapers station, for example.
 */
export function PublishStudio({
  onClose,
  initialStage = "source",
}: {
  onClose: () => void;
  initialStage?: Stage;
}) {
  const t = useT();
  const localeReady = useLocaleChunk("marketplace");
  const queryClient = useQueryClient();
  const identity = usePublishIdentity();

  const [stage, setStage] = useState<Stage>(initialStage);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [prefill, setPrefill] = useState<{
    origin: string;
    files: string[];
    warnings: string[];
  } | null>(null);
  const [errors, setErrors] = useState<FieldErrorWire[]>([]);
  const [checked, setChecked] = useState(false);
  const [result, setResult] = useState<SubmitResultWire | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
    setChecked(false);
  };

  const applyFolderDraft = (folder: FolderDraft, origin: string) => {
    setDraft({
      kind: folder.kind,
      name: folder.name,
      version: folder.version,
      title: folder.title,
      description: folder.description,
      categories: folder.categories,
      skill_md: folder.skill_md,
      plugin_json_text: folder.plugin_json_text,
      mcp_json_text: folder.mcp_json_text,
      usage_card: folder.usage_card,
      skills: folder.skills,
    });
    setPrefill({ origin, files: folder.files, warnings: folder.warnings });
    setErrors([]);
    setChecked(false);
    setStage("form");
  };

  const startBlank = (kind: Kind) => {
    setDraft({
      ...EMPTY_DRAFT,
      kind,
      skill_md: kind === "skill" ? SKILL_TEMPLATE : "",
      plugin_json_text: kind === "plugin" ? PLUGIN_TEMPLATE : "",
      mcp_json_text: kind === "plugin" ? MCP_TEMPLATE : "",
    });
    setPrefill(null);
    setErrors([]);
    setChecked(false);
    setStage("form");
  };

  const startOver = () => {
    setStage("source");
    setDraft(EMPTY_DRAFT);
    setPrefill(null);
    setErrors([]);
    setChecked(false);
    setResult(null);
  };

  const validateMutation = useMutation({
    mutationFn: async (): Promise<FieldErrorWire[]> => {
      const converted = draftToBody(draft, t);
      if ("parseError" in converted) return [converted.parseError];
      const res = await fetch("/api/marketplace/publish/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(converted.body),
      });
      if (!res.ok) throw new Error(`Validation request failed (${res.status})`);
      const data = (await res.json()) as { errors: FieldErrorWire[] };
      return data.errors;
    },
    onSuccess: (errs) => {
      setErrors(errs);
      setChecked(true);
    },
  });

  const submitMutation = useMutation({
    mutationFn: async (): Promise<SubmitResultWire> => {
      const converted = draftToBody(draft, t);
      if ("parseError" in converted) {
        setErrors([converted.parseError]);
        throw new Error(converted.parseError.error);
      }
      const res = await fetch("/api/marketplace/publish/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(converted.body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = (data as { detail?: { error?: string; field?: string | null } }).detail;
        if (detail?.error) {
          setErrors([{ field: detail.field ?? null, error: detail.error }]);
          throw new Error(detail.error);
        }
        throw new Error(`Publish failed (${res.status})`);
      }
      return data as SubmitResultWire;
    },
    onSuccess: (r) => {
      setResult(r);
      setErrors([]);
      setStage("done");
      queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
    },
  });

  const signedIn = identity.data?.signed_in === true;
  const disabled = identity.data ? !identity.data.enabled : false;

  // The rail: which station is lit. "live" only after a publish went through.
  const station: 1 | 2 | 3 | 4 =
    stage === "done" ? 4 : stage === "form" ? 3 : signedIn ? 2 : 1;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("marketplace.studio_title")}
      data-testid="publish-studio"
      className="fixed inset-0 z-[60] flex bg-background/80 backdrop-blur-md"
    >
      <button
        type="button"
        aria-label={t("marketplace.close")}
        onClick={onClose}
        className="absolute inset-0 cursor-default"
      />
      <div className="relative m-auto flex h-[min(92vh,56rem)] w-[min(96vw,72rem)] overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-2xl">
        {/* Rail */}
        <aside className="relative isolate hidden w-60 shrink-0 flex-col border-r border-border bg-secondary/30 p-5 md:flex">
          <div
            aria-hidden
            className="pointer-events-none absolute -z-10 -left-20 top-0 h-64 w-64 rounded-full bg-primary/10 blur-3xl"
          />
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {t("marketplace.studio_eyebrow")}
          </p>
          <h2 className="mt-1 font-display text-lg font-semibold tracking-tight text-foreground">
            {t("marketplace.studio_title")}
          </h2>
          <ol className="mt-6 space-y-1">
            <Station n={1} active={station === 1} done={signedIn} label={t("marketplace.studio_station_who")} />
            <Station n={2} active={station === 2} done={station > 2} label={t("marketplace.studio_station_what")} />
            <Station n={3} active={station === 3} done={station > 3} label={t("marketplace.studio_station_check")} />
            <Station n={4} active={station === 4} done={false} label={t("marketplace.studio_station_live")} />
          </ol>
          <div className="mt-auto space-y-2 text-[11px] leading-relaxed text-muted-foreground">
            <p>{t("marketplace.studio_rail_note_public")}</p>
            <p>{t("marketplace.studio_rail_note_rules")}</p>
          </div>
        </aside>

        {/* Work area */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex items-center gap-3 border-b border-border px-5 py-3">
            {stage !== "source" && stage !== "done" && (
              <Button size="sm" variant="ghost" onClick={() => setStage("source")}>
                <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
                {t("marketplace.studio_back")}
              </Button>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-foreground">
                {stage === "source" && t("marketplace.studio_h_source")}
                {stage === "github" && t("marketplace.studio_h_github")}
                {stage === "wallpapers" && t("marketplace.studio_h_wallpapers")}
                {stage === "form" && t("marketplace.studio_h_form")}
                {stage === "done" && t("marketplace.studio_h_done")}
              </p>
            </div>
            {signedIn && identity.data && (
              <span className="hidden items-center gap-2 rounded-full border border-border bg-background/60 py-0.5 pl-0.5 pr-2.5 text-[11px] text-foreground sm:flex">
                <PublisherAvatar login={identity.data.login} url={identity.data.avatar_url} size={20} />
                @{identity.data.login}
              </span>
            )}
            <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("marketplace.close")}>
              <X className="h-4 w-4" />
            </Button>
          </header>

          <ScrollArea className="min-h-0 flex-1">
            <div className="px-5 py-5">
              {!localeReady ? (
                <p className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                </p>
              ) : disabled ? (
                <p className="text-sm text-muted-foreground">{t("marketplace.studio_disabled")}</p>
              ) : stage === "done" && result ? (
                <PublishedCard result={result} onPublishAnother={startOver} t={t} />
              ) : (
                <div className="space-y-5">
                  {stage !== "wallpapers" && (
                    <PublishIdentityCard identity={identity.data} loading={identity.isLoading} />
                  )}

                  {stage === "source" && (
                    <SourcePicker
                      onFolder={applyFolderDraft}
                      onGithub={() => setStage("github")}
                      onBlank={startBlank}
                      onWallpapers={() => setStage("wallpapers")}
                      wallpapersEnabled={identity.data?.wallpapers_enabled !== false}
                      t={t}
                    />
                  )}

                  {stage === "github" && (
                    <GithubImportPanel
                      login={signedIn ? (identity.data?.login ?? "") : ""}
                      onImported={applyFolderDraft}
                      t={t}
                    />
                  )}

                  {stage === "wallpapers" && (
                    <WallpaperLane identityLoading={identity.isLoading} identity={identity.data} t={t} />
                  )}

                  {stage === "form" && (
                    <DraftForm
                      draft={draft}
                      set={set}
                      prefill={prefill}
                      errors={errors}
                      checked={checked}
                      signedIn={signedIn}
                      validating={validateMutation.isPending}
                      submitting={submitMutation.isPending}
                      onCheck={() => validateMutation.mutate()}
                      onPublish={() => submitMutation.mutate()}
                      onStartOver={startOver}
                      t={t}
                    />
                  )}
                </div>
              )}
            </div>
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}

function Station({
  n,
  active,
  done,
  label,
}: {
  n: number;
  active: boolean;
  done: boolean;
  label: string;
}) {
  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm transition-colors",
        active ? "bg-background/70 text-foreground shadow-sm" : "text-muted-foreground",
      )}
      data-active={active ? "yes" : "no"}
    >
      <span
        className={cn(
          "grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[11px] font-bold",
          done
            ? "border-primary bg-primary text-primary-foreground"
            : active
              ? "border-primary text-primary"
              : "border-border text-muted-foreground",
        )}
      >
        {done ? <Check className="h-3 w-3" /> : n}
      </span>
      <span className="truncate">{label}</span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Station 2: where does the thing come from?
// ---------------------------------------------------------------------------

function SourcePicker({
  onFolder,
  onGithub,
  onBlank,
  onWallpapers,
  wallpapersEnabled,
  t,
}: {
  onFolder: (draft: FolderDraft, origin: string) => void;
  onGithub: () => void;
  onBlank: (kind: Kind) => void;
  onWallpapers: () => void;
  wallpapersEnabled: boolean;
  t: Translate;
}) {
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dropError, setDropError] = useState<string | null>(null);
  const pickerRef = useRef<HTMLInputElement | null>(null);

  const handleFiles = async (
    load: () => Promise<Parameters<typeof folderToDraft>[0]>,
    origin: string,
  ) => {
    setBusy(true);
    setDropError(null);
    try {
      const files = await load();
      const folder = await folderToDraft(files);
      onFolder(folder, origin);
    } catch (err) {
      setDropError(err instanceof Error ? err.message : t("marketplace.studio_err_folder"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-foreground">
          {t("marketplace.studio_source_label")}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {t("marketplace.studio_source_hint")}
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {/* Folder drop — the primary door, so it gets the big tile. */}
        <div
          role="button"
          tabIndex={0}
          aria-label={t("marketplace.studio_door_folder")}
          data-testid="studio-door-folder"
          onClick={() => pickerRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") pickerRef.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            const items = e.dataTransfer.items;
            void handleFiles(() => collectDroppedFiles(items), t("marketplace.studio_origin_folder"));
          }}
          className={cn(
            "group relative isolate flex min-h-44 cursor-pointer flex-col justify-end overflow-hidden rounded-2xl border border-dashed p-5 text-left transition-colors md:row-span-2",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            dragOver
              ? "border-primary bg-primary/10"
              : "border-border bg-gradient-to-br from-primary/10 via-card to-card hover:border-primary/50",
          )}
        >
          <div
            aria-hidden
            className="pointer-events-none absolute -z-10 -right-10 -top-10 h-40 w-40 rounded-full bg-primary/15 blur-3xl transition-transform duration-500 group-hover:scale-125"
          />
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-primary text-primary-foreground shadow-lg">
            {busy ? <Loader2 className="h-5 w-5 animate-spin" /> : <FolderUp className="h-5 w-5" />}
          </span>
          <p className="mt-4 font-display text-base font-semibold tracking-tight text-foreground">
            {t("marketplace.studio_door_folder")}
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {t("marketplace.studio_door_folder_hint")}
          </p>
          <input
            ref={pickerRef}
            type="file"
            multiple
            className="hidden"
            // Non-standard but universally supported attribute for folder pickers.
            {...({ webkitdirectory: "" } as Record<string, string>)}
            onChange={(e) => {
              const list = e.currentTarget.files;
              if (list && list.length > 0) {
                void handleFiles(
                  () => Promise.resolve(collectPickedFiles(list)),
                  t("marketplace.studio_origin_folder"),
                );
              }
              e.currentTarget.value = "";
            }}
          />
        </div>

        <Door
          icon={<Github className="h-4 w-4" />}
          title={t("marketplace.studio_door_github")}
          hint={t("marketplace.studio_door_github_hint")}
          onClick={onGithub}
          testId="studio-door-github"
        />

        <div className="flex flex-col rounded-2xl border border-border bg-card/50 p-4">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-secondary text-foreground">
            <PencilLine className="h-4 w-4" />
          </span>
          <p className="mt-3 text-sm font-semibold text-foreground">
            {t("marketplace.studio_door_blank")}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("marketplace.studio_door_blank_hint")}
          </p>
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => onBlank("skill")}>
              <FileText className="mr-1.5 h-3.5 w-3.5" />
              {t("marketplace.kind_skill")}
            </Button>
            <Button size="sm" variant="outline" onClick={() => onBlank("plugin")}>
              <Plug className="mr-1.5 h-3.5 w-3.5" />
              {t("marketplace.kind_plugin")}
            </Button>
          </div>
        </div>

        {wallpapersEnabled && (
          <Door
            icon={<ImageIcon className="h-4 w-4" />}
            title={t("marketplace.studio_door_wallpaper")}
            hint={t("marketplace.studio_door_wallpaper_hint")}
            onClick={onWallpapers}
            testId="studio-door-wallpaper"
            className="md:col-span-2"
          />
        )}
      </div>

      {dropError && (
        <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {dropError}
        </p>
      )}

      <HowPublishingWorks t={t} />
    </section>
  );
}

function Door({
  icon,
  title,
  hint,
  onClick,
  testId,
  className,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
  onClick: () => void;
  testId?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "flex flex-col rounded-2xl border border-border bg-card/50 p-4 text-left transition-colors",
        "hover:border-primary/50 hover:bg-secondary/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      <span className="grid h-9 w-9 place-items-center rounded-lg bg-secondary text-foreground">
        {icon}
      </span>
      <p className="mt-3 text-sm font-semibold text-foreground">{title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
    </button>
  );
}

/** The built-in author guide: the four steps and the expected folder shape,
 *  inline, so nobody needs to find a doc first. */
function HowPublishingWorks({ t }: { t: Translate }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="rounded-2xl border border-border bg-card/40 p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-foreground">
        {t("marketplace.studio_how_title")}
      </p>
      <ol className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-4">
        {[1, 2, 3, 4].map((n) => (
          <li key={n} className="flex gap-2">
            <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full border border-primary/50 text-[10px] font-bold text-primary">
              {n}
            </span>
            {t(`marketplace.studio_how_step_${n}`)}
          </li>
        ))}
      </ol>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="mt-3 text-[11px] font-medium text-primary hover:underline"
      >
        {open ? t("marketplace.studio_how_hide_layout") : t("marketplace.studio_how_show_layout")}
      </button>
      {open && (
        <div className="mt-2 overflow-x-auto">
          <pre className="rounded-md border border-border bg-background p-3 font-mono text-[11px] leading-relaxed text-foreground">
            {`my-plugin/
├── plugin.json                  ← required: name, description, version
├── mcp.json                     ← the server your plugin talks to (optional)
├── skills/                      ← instructions that ship with it (optional)
│   └── my-skill/SKILL.md
└── io.github.personaljarvis/
    └── usage-card.md            ← when the assistant should use it (optional)

my-skill/
└── SKILL.md                     ← a standalone skill: one file, YAML frontmatter first`}
          </pre>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {t("marketplace.studio_how_layout_note")}
          </p>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// GitHub import: repos → candidates → prefilled draft
// ---------------------------------------------------------------------------

function GithubImportPanel({
  login,
  onImported,
  t,
}: {
  login: string;
  onImported: (draft: FolderDraft, origin: string) => void;
  t: Translate;
}) {
  const [account, setAccount] = useState(login);
  const [refText, setRefText] = useState("");
  const [repos, setRepos] = useState<RepoSummary[] | null>(null);
  const [selected, setSelected] = useState<{ owner: string; repo: string; branch: string } | null>(
    null,
  );
  const [candidates, setCandidates] = useState<RepoCandidate[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadRepos = async (user: string) => {
    if (!user.trim()) return;
    setBusy("repos");
    setError(null);
    setSelected(null);
    setCandidates(null);
    setTruncated(false);
    try {
      setRepos(await listRepos(user.trim()));
    } catch (err) {
      setRepos(null);
      setError(err instanceof Error ? err.message : t("marketplace.studio_err_repos"));
    } finally {
      setBusy(null);
    }
  };

  // Auto-load the signed-in account's repos once.
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (login && !autoLoaded.current) {
      autoLoaded.current = true;
      void loadRepos(login);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [login]);

  const openRepo = async (owner: string, repo: string, branch: string) => {
    setBusy(`scan:${owner}/${repo}`);
    setError(null);
    setSelected({ owner, repo, branch });
    setCandidates(null);
    setTruncated(false);
    try {
      const { candidates: found, truncated: cutOff } = await scanRepo(owner, repo, branch);
      setCandidates(found);
      setTruncated(cutOff);
      if (found.length === 0) {
        setError(
          cutOff ? t("marketplace.studio_err_scan_truncated") : t("marketplace.studio_err_scan_empty"),
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("marketplace.studio_err_scan"));
    } finally {
      setBusy(null);
    }
  };

  const importCandidate = async (candidate: RepoCandidate) => {
    if (!selected) return;
    setBusy(`import:${candidate.dir}`);
    setError(null);
    try {
      const prefix = candidate.dir ? `${candidate.dir}/` : "";
      const files = await Promise.all(
        candidate.paths.map(async (path) => {
          const text = await fetchRepoFile(selected.owner, selected.repo, selected.branch, path);
          return { path: path.slice(prefix.length), file: new File([text], path) };
        }),
      );
      const draft = await folderToDraft(files);
      onImported(draft, `${selected.owner}/${selected.repo}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("marketplace.studio_err_import"));
    } finally {
      setBusy(null);
    }
  };

  const openRef = async () => {
    const parsed = parseRepoRef(refText);
    if (!parsed) {
      setError(t("marketplace.studio_err_ref"));
      return;
    }
    // The git trees API needs a real branch name, not the literal "HEAD" —
    // resolve the repo's default branch first.
    setBusy(`resolve:${parsed.owner}/${parsed.repo}`);
    setError(null);
    try {
      const info = await getRepo(parsed.owner, parsed.repo);
      await openRepo(parsed.owner, parsed.repo, info.default_branch);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("marketplace.studio_err_resolve"));
      setBusy(null);
    }
  };

  return (
    <section className="space-y-4 rounded-2xl border border-border bg-card/40 p-4">
      <p className="text-[11px] text-muted-foreground">{t("marketplace.studio_github_note")}</p>

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1">
          <label className="mb-1 block text-xs font-medium text-foreground" htmlFor="gh-account">
            {t("marketplace.studio_github_account")}
          </label>
          <input
            id="gh-account"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void loadRepos(account);
            }}
            placeholder="your-github-name"
            className={inputCls(false)}
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void loadRepos(account)}
          disabled={busy === "repos" || !account.trim()}
        >
          {busy === "repos" ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Search className="mr-1.5 h-3.5 w-3.5" />
          )}
          {t("marketplace.studio_github_list")}
        </Button>
        <div className="min-w-48 flex-1">
          <label className="mb-1 block text-xs font-medium text-foreground" htmlFor="gh-ref">
            {t("marketplace.studio_github_one_repo")}
          </label>
          <input
            id="gh-ref"
            value={refText}
            onChange={(e) => setRefText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void openRef();
            }}
            placeholder="owner/repo"
            className={inputCls(false)}
          />
        </div>
        <Button size="sm" variant="outline" onClick={() => void openRef()} disabled={busy !== null}>
          {busy?.startsWith("resolve:") ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : null}
          {t("marketplace.studio_github_scan")}
        </Button>
      </div>

      {error && (
        <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      )}

      {repos && !selected && (
        <ul className="max-h-72 space-y-1 overflow-y-auto">
          {repos.map((r) => (
            <li key={r.full_name}>
              <button
                type="button"
                onClick={() => {
                  const [owner, repo] = r.full_name.split("/");
                  void openRepo(owner, repo, r.default_branch);
                }}
                className={cn(
                  "flex w-full items-baseline gap-2 rounded-md border border-transparent px-2 py-1.5 text-left transition-colors",
                  "hover:border-border hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                )}
              >
                <span className="font-mono text-xs text-foreground">{r.full_name}</span>
                {busy === `scan:${r.full_name}` && (
                  <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                )}
                {r.description && (
                  <span className="truncate text-[11px] text-muted-foreground">
                    {r.description}
                  </span>
                )}
              </button>
            </li>
          ))}
          {repos.length === 0 && (
            <li className="px-2 py-1.5 text-xs text-muted-foreground">
              {t("marketplace.studio_github_no_repos")}
            </li>
          )}
        </ul>
      )}

      {selected && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setSelected(null);
                setCandidates(null);
                setTruncated(false);
                setError(null);
              }}
              className="text-[11px] font-medium text-primary hover:underline"
            >
              ← {t("marketplace.studio_github_all_repos")}
            </button>
            <span className="font-mono text-xs text-foreground">
              {selected.owner}/{selected.repo}
            </span>
            {busy?.startsWith("scan:") && (
              <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
            )}
          </div>
          {truncated && candidates && candidates.length > 0 && (
            <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              {t("marketplace.studio_github_truncated")}
            </p>
          )}
          {candidates && candidates.length > 0 && (
            <ul className="space-y-1">
              {candidates.map((c) => (
                <li
                  key={`${c.kind}:${c.dir}`}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background px-2.5 py-2"
                >
                  {c.kind === "plugin" ? (
                    <Plug className="h-3.5 w-3.5 text-primary" />
                  ) : (
                    <FileText className="h-3.5 w-3.5 text-primary" />
                  )}
                  <span className="text-xs font-medium text-foreground">
                    {c.kind === "plugin" ? t("marketplace.kind_plugin") : t("marketplace.kind_skill")}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">/{c.dir || ""}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {fill(t("marketplace.studio_github_files"), { count: c.paths.length })}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    className="ml-auto"
                    onClick={() => void importCandidate(c)}
                    disabled={busy !== null}
                  >
                    {busy === `import:${c.dir}` ? (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    ) : null}
                    {t("marketplace.studio_github_review")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// The wallpaper lane: your own pictures, one click from published.
// ---------------------------------------------------------------------------

function WallpaperLane({
  identity,
  identityLoading,
  t,
}: {
  identity: ReturnType<typeof usePublishIdentity>["data"];
  identityLoading: boolean;
  t: Translate;
}) {
  const { data: uploads, isLoading } = useWallpaperUploads();
  const [shareItem, setShareItem] = useState<WallpaperEntry | null>(null);
  const own = useMemo(
    () => (uploads ?? []).filter((u) => u.source !== "marketplace").map(uploadAsEntry),
    [uploads],
  );
  return (
    <section className="space-y-4">
      <PublishIdentityCard
        identity={identity}
        loading={identityLoading}
        blurb={t("marketplace.share_identity_blurb")}
      />
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-foreground">
          {t("marketplace.studio_wallpapers_label")}
        </span>
        <span className="text-[11px] text-muted-foreground">
          {t("marketplace.studio_wallpapers_hint")}
        </span>
      </div>
      {isLoading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          {t("marketplace.loading")}
        </p>
      ) : own.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border p-6 text-center">
          <ImageIcon className="mx-auto mb-2 h-5 w-5 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">{t("marketplace.studio_wallpapers_empty")}</p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="studio-own-wallpapers">
          {own.map((entry) => (
            <button
              key={entry.id}
              type="button"
              onClick={() => setShareItem(entry)}
              className="group relative aspect-[16/10] overflow-hidden rounded-xl border border-border bg-secondary/40 text-left transition-colors hover:border-primary/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <img
                src={thumbUrlFor(entry)}
                alt=""
                loading="lazy"
                className="h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
              />
              <span className="absolute inset-x-0 bottom-0 flex items-center gap-2 bg-gradient-to-t from-black/75 to-transparent p-3">
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-white">
                  {entry.title}
                </span>
                <span className="rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-medium text-white backdrop-blur">
                  {t("marketplace.share_cta")}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
      {shareItem && <PublishWallpaperDialog item={shareItem} onClose={() => setShareItem(null)} />}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Station 3: the form (prefilled or blank) + live store-card preview
// ---------------------------------------------------------------------------

function DraftForm({
  draft,
  set,
  prefill,
  errors,
  checked,
  signedIn,
  validating,
  submitting,
  onCheck,
  onPublish,
  onStartOver,
  t,
}: {
  draft: Draft;
  set: (patch: Partial<Draft>) => void;
  prefill: { origin: string; files: string[]; warnings: string[] } | null;
  errors: FieldErrorWire[];
  checked: boolean;
  signedIn: boolean;
  validating: boolean;
  submitting: boolean;
  onCheck: () => void;
  onPublish: () => void;
  onStartOver: () => void;
  t: Translate;
}) {
  const errorFor = (field: string) => errors.filter((e) => e.field === field);
  const generalErrors = errors.filter((e) => e.field === null);
  const skillBytes = new TextEncoder().encode(draft.skill_md).length;

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <section className="space-y-4" data-testid="studio-form">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-foreground">
            {t("marketplace.studio_form_label")}
          </span>
          <div className="ml-auto flex items-center gap-1">
            <KindButton
              active={draft.kind === "skill"}
              icon={<FileText className="h-3.5 w-3.5" />}
              label={t("marketplace.kind_skill")}
              onClick={() => set({ kind: "skill" })}
            />
            <KindButton
              active={draft.kind === "plugin"}
              icon={<Plug className="h-3.5 w-3.5" />}
              label={t("marketplace.kind_plugin")}
              onClick={() => set({ kind: "plugin" })}
            />
          </div>
        </div>

        {prefill && (
          <div className="rounded-xl border border-primary/30 bg-primary/5 px-3 py-2">
            <p className="text-xs text-foreground">
              <Check className="mr-1 inline h-3.5 w-3.5 text-primary" />
              {fill(t("marketplace.studio_prefilled_from"), { origin: prefill.origin })}
            </p>
            {prefill.files.length > 0 && (
              <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                {prefill.files.join(" · ")}
              </p>
            )}
            {prefill.warnings.map((w, i) => (
              <p key={i} className="mt-1 flex items-start gap-1.5 text-[11px] text-muted-foreground">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                {w}
              </p>
            ))}
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label={t("marketplace.studio_f_name")}
            hint={t("marketplace.studio_f_name_hint")}
            errors={errorFor("name")}
          >
            <input
              value={draft.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="my-skill"
              className={inputCls(errorFor("name").length > 0)}
              data-testid="studio-name"
            />
          </Field>
          <Field
            label={t("marketplace.studio_f_version")}
            hint={t("marketplace.studio_f_version_hint")}
            errors={errorFor("version")}
          >
            <input
              value={draft.version}
              onChange={(e) => set({ version: e.target.value })}
              placeholder="1.0.0"
              className={inputCls(errorFor("version").length > 0)}
            />
          </Field>
        </div>

        {draft.kind === "skill" ? (
          <>
            <Field label={t("marketplace.studio_f_title")} errors={errorFor("title")}>
              <input
                value={draft.title}
                onChange={(e) => set({ title: e.target.value })}
                placeholder="My Skill"
                className={inputCls(errorFor("title").length > 0)}
              />
            </Field>
            <Field
              label={t("marketplace.studio_f_description")}
              hint={fill(t("marketplace.studio_f_description_hint"), {
                count: draft.description.length,
              })}
              errors={errorFor("description")}
            >
              <textarea
                value={draft.description}
                onChange={(e) => set({ description: e.target.value })}
                rows={2}
                className={inputCls(errorFor("description").length > 0)}
              />
            </Field>
            <Field
              label={t("marketplace.studio_f_categories")}
              hint={t("marketplace.studio_f_categories_hint")}
              errors={[]}
            >
              <input
                value={draft.categories}
                onChange={(e) => set({ categories: e.target.value })}
                placeholder="writing, research"
                className={inputCls(false)}
              />
            </Field>
            <Field
              label="SKILL.md"
              hint={fill(t("marketplace.studio_f_skill_md_hint"), {
                kb: (skillBytes / 1024).toFixed(1),
                max: MAX_SKILL_MD_BYTES / 1024,
              })}
              errors={errorFor("skill_md")}
              action={
                draft.skill_md.trim() === "" ? (
                  <button
                    type="button"
                    onClick={() => set({ skill_md: SKILL_TEMPLATE })}
                    className="text-[11px] font-medium text-primary hover:underline"
                  >
                    {t("marketplace.studio_insert_template")}
                  </button>
                ) : null
              }
            >
              <textarea
                value={draft.skill_md}
                onChange={(e) => set({ skill_md: e.target.value })}
                rows={12}
                spellCheck={false}
                className={cn(inputCls(errorFor("skill_md").length > 0), "font-mono text-xs")}
              />
            </Field>
          </>
        ) : (
          <>
            <Field
              label="plugin.json"
              hint={t("marketplace.studio_f_plugin_json_hint")}
              errors={errorFor("plugin_json")}
            >
              <textarea
                value={draft.plugin_json_text}
                onChange={(e) => set({ plugin_json_text: e.target.value })}
                rows={10}
                spellCheck={false}
                placeholder='{ "name": "my-plugin", "version": "1.0.0", … }'
                className={cn(inputCls(errorFor("plugin_json").length > 0), "font-mono text-xs")}
              />
            </Field>
            <Field
              label="mcp.json"
              hint={t("marketplace.studio_f_mcp_json_hint")}
              errors={errorFor("mcp_json")}
            >
              <textarea
                value={draft.mcp_json_text}
                onChange={(e) => set({ mcp_json_text: e.target.value })}
                rows={6}
                spellCheck={false}
                placeholder='{ "mcpServers": { "my-plugin": { "type": "streamable-http", "url": "https://…" } } }'
                className={cn(inputCls(errorFor("mcp_json").length > 0), "font-mono text-xs")}
              />
            </Field>

            <Field
              label={t("marketplace.studio_f_skills")}
              hint={t("marketplace.studio_f_skills_hint")}
              errors={errorFor("skills")}
            >
              {draft.skills.length === 0 ? (
                <p className="rounded-md border border-dashed border-border px-2.5 py-2 text-[11px] text-muted-foreground">
                  {t("marketplace.studio_f_skills_none")}
                </p>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {draft.skills.map((s) => (
                    <li
                      key={s.name}
                      className="flex items-center gap-1.5 rounded-full border border-border bg-background px-2.5 py-1"
                    >
                      <FileText className="h-3 w-3 text-primary" />
                      <span className="font-mono text-[11px] text-foreground">{s.name}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {Math.max(1, Math.round(s.skill_md.length / 1024))} KB
                      </span>
                      <button
                        type="button"
                        aria-label={`Remove ${s.name}`}
                        onClick={() =>
                          set({ skills: draft.skills.filter((x) => x.name !== s.name) })
                        }
                        className="grid h-4 w-4 place-items-center rounded text-muted-foreground hover:text-foreground"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Field>

            <Field
              label={t("marketplace.studio_f_usage_card")}
              hint={t("marketplace.studio_f_usage_card_hint")}
              errors={[]}
            >
              <textarea
                value={draft.usage_card}
                onChange={(e) => set({ usage_card: e.target.value })}
                rows={4}
                spellCheck={false}
                className={cn(inputCls(false), "font-mono text-xs")}
              />
            </Field>
          </>
        )}

        {generalErrors.map((e, i) => (
          <p
            key={i}
            className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {e.error}
          </p>
        ))}
        {checked && errors.length === 0 && (
          <p
            className="flex items-center gap-2 rounded-md border border-primary/40 bg-primary/5 px-3 py-2 text-xs text-foreground"
            data-testid="studio-check-ok"
          >
            <Check className="h-3.5 w-3.5 text-primary" />
            {t("marketplace.studio_check_ok")}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/50 pt-3">
          <Button size="sm" variant="ghost" onClick={onStartOver}>
            <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
            {t("marketplace.studio_start_over")}
          </Button>
          <Button size="sm" variant="outline" onClick={onCheck} disabled={validating}>
            {validating ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            {t("marketplace.studio_check")}
          </Button>
          <Button
            size="sm"
            onClick={onPublish}
            disabled={submitting || !signedIn}
            title={signedIn ? undefined : t("marketplace.studio_sign_in_first")}
            data-testid="studio-publish"
          >
            {submitting ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
            )}
            {t("marketplace.studio_publish")}
          </Button>
        </div>
      </section>

      <aside className="lg:sticky lg:top-0 lg:self-start">
        <CardPreview draft={draft} t={t} />
      </aside>
    </div>
  );
}

/** What the store card will look like — the author sees the listing before
 *  the world does. Derived from the draft on every keystroke. */
function CardPreview({ draft, t }: { draft: Draft; t: Translate }) {
  const preview = useMemo(() => {
    if (draft.kind === "skill") {
      return {
        title: draft.title.trim() || draft.name.trim() || t("marketplace.studio_preview_untitled"),
        description: draft.description.trim() || t("marketplace.studio_preview_no_description"),
        detail: draft.categories
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean)
          .join(" · "),
        initial: (draft.title.trim() || draft.name.trim() || "S").slice(0, 1).toUpperCase(),
      };
    }
    let displayName = draft.name.trim();
    let description = "";
    let category = "Community";
    try {
      const manifest = JSON.parse(draft.plugin_json_text || "{}") as {
        description?: string;
        extensions?: Record<string, { display_name?: string; category?: string }>;
      };
      description = manifest.description ?? "";
      const ext = manifest.extensions?.["io.github.personaljarvis"];
      if (ext?.display_name) displayName = ext.display_name;
      if (ext?.category) category = ext.category;
    } catch {
      // Preview only — the real validation reports JSON errors properly.
    }
    let server = "";
    try {
      const mcp = JSON.parse(draft.mcp_json_text || "{}") as {
        mcpServers?: Record<string, { url?: string; command?: string; args?: string[] }>;
      };
      const first = Object.values(mcp.mcpServers ?? {})[0];
      if (first?.url) server = first.url;
      else if (first?.command) server = [first.command, ...(first.args ?? [])].join(" ");
    } catch {
      // Preview only.
    }
    const bits = [category];
    if (draft.skills.length > 0) {
      bits.push(fill(t("marketplace.studio_preview_bundled"), { count: draft.skills.length }));
    }
    if (server) bits.push(server);
    return {
      title: displayName || t("marketplace.studio_preview_untitled"),
      description: description || t("marketplace.studio_preview_no_description"),
      detail: bits.join(" · "),
      initial: (displayName || "P").slice(0, 1).toUpperCase(),
    };
  }, [draft, t]);

  return (
    <div data-testid="studio-card-preview">
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t("marketplace.studio_preview_label")}
      </p>
      <div className="overflow-hidden rounded-2xl border border-border bg-card/70 shadow-sm">
        <div className="flex items-center gap-3 px-3.5 py-3">
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-primary/70 to-primary/30 text-sm font-semibold text-primary-foreground">
            {draft.kind === "skill" ? <Sparkles className="h-4 w-4" /> : preview.initial}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">{preview.title}</span>
              <span className="shrink-0 rounded-full border border-border px-1.5 py-px text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
                {draft.kind === "skill" ? t("marketplace.kind_skill") : t("marketplace.kind_plugin")}
              </span>
            </div>
            <p className="truncate text-xs text-muted-foreground">{preview.description}</p>
          </div>
        </div>
        <div className="flex items-center justify-between border-t border-border/70 px-3.5 py-2 text-[10px] text-muted-foreground">
          <span className="truncate">{preview.detail || "—"}</span>
          <span className="ml-2 shrink-0 tabular-nums">v{draft.version.trim() || "?"}</span>
        </div>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
        {t("marketplace.studio_preview_note")}
      </p>
    </div>
  );
}

function inputCls(hasError: boolean): string {
  return cn(
    "w-full rounded-lg border bg-background px-2.5 py-1.5 text-sm text-foreground",
    "placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-ring",
    hasError ? "border-destructive/60" : "border-border",
  );
}

function Field({
  label,
  hint,
  errors,
  action,
  children,
}: {
  label: string;
  hint?: string;
  errors: FieldErrorWire[];
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <label className="text-xs font-medium text-foreground">{label}</label>
        {action}
      </div>
      {children}
      {hint && errors.length === 0 && (
        <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>
      )}
      {errors.map((e, i) => (
        <p key={i} className="mt-1 text-[11px] text-destructive">
          {e.error}
        </p>
      ))}
    </div>
  );
}

function KindButton({
  active,
  icon,
  label,
  onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors",
        active
          ? "border-primary/50 bg-primary/10 text-foreground"
          : "border-border text-muted-foreground hover:border-primary/30 hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Station 4: the PR is open — watch the live feed until the package appears.
// ---------------------------------------------------------------------------

function PublishedCard({
  result,
  onPublishAnother,
  t,
}: {
  result: SubmitResultWire;
  onPublishAnother: () => void;
  t: Translate;
}) {
  const [live, setLive] = useState(false);
  const [waitedOut, setWaitedOut] = useState(false);

  useEffect(() => {
    if (live) return;
    let ticks = 0;
    const timer = setInterval(async () => {
      ticks += 1;
      if (ticks > 20) {
        // ~7 minutes of watching is enough; the PR link remains the source
        // of truth for anything slower (checks queued, merge conflict).
        setWaitedOut(true);
        clearInterval(timer);
        return;
      }
      try {
        const params = new URLSearchParams({
          name: result.name,
          version: result.version,
          force: "true",
        });
        const res = await fetch(`/api/marketplace/publish/status?${params.toString()}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const data = (await res.json()) as { live: boolean };
        if (data.live) {
          setLive(true);
          clearInterval(timer);
        }
      } catch {
        // Feed unreachable — keep trying until the tick budget runs out.
      }
    }, 20_000);
    return () => clearInterval(timer);
  }, [live, result]);

  return (
    <div className="space-y-4" data-testid="studio-published">
      <section className="relative isolate overflow-hidden rounded-2xl border border-primary/30 bg-gradient-to-br from-primary/15 via-card to-card p-5">
        <div
          aria-hidden
          className="pointer-events-none absolute -z-10 -right-16 -top-16 h-56 w-56 rounded-full bg-primary/20 blur-3xl"
        />
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          {t("marketplace.studio_done_eyebrow")}
        </p>
        <p className="mt-1 flex items-center gap-2 font-display text-lg font-semibold tracking-tight text-foreground">
          <Check className="h-5 w-5 text-primary" />
          {result.name} <span className="text-muted-foreground">v{result.version}</span>
        </p>
        <p className="mt-2 max-w-xl text-xs leading-relaxed text-muted-foreground">
          {t("marketplace.studio_done_body")}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {result.pr_url && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => openExternalUrl(result.pr_url ?? "")}
            >
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              {t("marketplace.studio_done_view_pr")}
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onPublishAnother}>
            {t("marketplace.studio_done_another")}
          </Button>
        </div>
      </section>

      <section className="space-y-4 rounded-2xl border border-border bg-card/40 p-4">
        {live ? (
          <p className="flex items-center gap-2 text-sm text-foreground">
            <Check className="h-4 w-4 text-primary" />
            {fill(t("marketplace.studio_done_live"), { name: result.name, version: result.version })}
          </p>
        ) : waitedOut ? (
          <p className="text-xs text-muted-foreground">{t("marketplace.studio_done_waited_out")}</p>
        ) : (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
            </span>
            {t("marketplace.studio_done_watching")}
          </p>
        )}

        {/* Gated on `live` on purpose: handed out a minute too early, the
            command resolves against an index that does not list the entry yet
            and fails for whoever the author sent it to. */}
        {live && result.install && (
          <InstallStandard
            install={result.install}
            heading={t("marketplace.studio_done_share_heading")}
            note={t("marketplace.studio_done_share_note")}
          />
        )}
      </section>
    </div>
  );
}
