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
  Loader2,
  PencilLine,
  Plug,
  Search,
  UploadCloud,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  InstallStandard,
  type InstallStandardWire,
} from "@/components/InstallStandard";
import {
  PublishIdentityCard,
  usePublishIdentity,
} from "@/components/marketplace/PublishIdentity";
import { cn } from "@/lib/utils";
import { openExternalUrl } from "@/lib/openExternal";
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
// Publish tab — the in-app half of the marketplace's publish path.
//
// The author never starts at an empty form: the entry screen offers the three
// ways a package can arrive — drop the folder, import it from a public GitHub
// repo, or write it by hand — and "the folder is the classification"
// (publishing-plan.md §2): what is inside decides whether it becomes a skill
// or a plugin card. Anyone may publish: whatever passes the automated checks
// goes live with no human review, so the copy stays honest about the two
// things that matter — the upload appears PUBLICLY under the user's GitHub
// name, and the only gate is the rule check. Identity comes from the GitHub
// device flow; the submission travels through the same storefront endpoint
// the web form uses, so web and app cannot drift apart.
// ---------------------------------------------------------------------------

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
type Source = "picker" | "github" | "form";

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

/** The JSON body the backend's validate/submit routes expect, or a field
 *  error when a pasted JSON block does not parse. */
function draftToBody(
  draft: Draft,
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
    return { parseError: { field: "plugin_json", error: "plugin.json is not valid JSON" } };
  }
  let mcpJson: unknown = null;
  try {
    mcpJson = draft.mcp_json_text.trim() ? JSON.parse(draft.mcp_json_text) : null;
  } catch {
    return { parseError: { field: "mcp_json", error: "mcp.json is not valid JSON" } };
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

export function PublishTab() {
  const queryClient = useQueryClient();
  const identity = usePublishIdentity();

  const [source, setSource] = useState<Source>("picker");
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [prefill, setPrefill] = useState<{ origin: string; files: string[]; warnings: string[] } | null>(
    null,
  );
  const [errors, setErrors] = useState<FieldErrorWire[]>([]);
  const [checked, setChecked] = useState(false);
  const [result, setResult] = useState<SubmitResultWire | null>(null);

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
    setSource("form");
  };

  const startBlank = (kind: Kind) => {
    setDraft({ ...EMPTY_DRAFT, kind });
    setPrefill(null);
    setErrors([]);
    setChecked(false);
    setSource("form");
  };

  const startOver = () => {
    setSource("picker");
    setDraft(EMPTY_DRAFT);
    setPrefill(null);
    setErrors([]);
    setChecked(false);
  };

  const validateMutation = useMutation({
    mutationFn: async (): Promise<FieldErrorWire[]> => {
      const converted = draftToBody(draft);
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
      const converted = draftToBody(draft);
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
    },
  });

  if (identity.data && !identity.data.enabled) {
    return (
      <p className="text-sm text-muted-foreground">
        Publishing is disabled in this deployment — no publish endpoint is
        configured.
      </p>
    );
  }

  if (result) {
    return (
      <PublishedCard
        result={result}
        onPublishAnother={() => {
          setResult(null);
          startOver();
          queryClient.invalidateQueries({ queryKey: ["marketplace-community"] });
        }}
      />
    );
  }

  return (
    <div className="space-y-6">
      <header>
        <h2 className="font-display text-base font-semibold tracking-tight text-foreground">
          Publish to the marketplace
        </h2>
        <p className="max-w-2xl text-xs text-muted-foreground">
          Anyone can publish. Whatever passes the automated checks goes live
          without human review, publicly under your GitHub name — installing
          users see exactly what your package would be allowed to do before
          they accept it.
        </p>
      </header>

      <PublishIdentityCard identity={identity.data} loading={identity.isLoading} />

      {source === "picker" && (
        <SourcePicker
          onFolder={applyFolderDraft}
          onGithub={() => setSource("github")}
          onBlank={startBlank}
        />
      )}

      {source === "github" && (
        <GithubImportPanel
          login={identity.data?.signed_in ? identity.data.login ?? "" : ""}
          onBack={() => setSource("picker")}
          onImported={applyFolderDraft}
        />
      )}

      {source === "form" && (
        <DraftForm
          draft={draft}
          set={set}
          prefill={prefill}
          errors={errors}
          checked={checked}
          signedIn={identity.data?.signed_in === true}
          validating={validateMutation.isPending}
          submitting={submitMutation.isPending}
          onCheck={() => validateMutation.mutate()}
          onPublish={() => submitMutation.mutate()}
          onStartOver={startOver}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1 after sign-in: where does the package come from?
// ---------------------------------------------------------------------------

function SourcePicker({
  onFolder,
  onGithub,
  onBlank,
}: {
  onFolder: (draft: FolderDraft, origin: string) => void;
  onGithub: () => void;
  onBlank: (kind: Kind) => void;
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
      setDropError(err instanceof Error ? err.message : "Could not read the folder.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-foreground">
          Where is your package?
        </span>
        <span className="text-[11px] text-muted-foreground">
          — what is inside decides whether it becomes a skill or a plugin card
        </span>
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {/* Folder drop */}
        <div
          role="button"
          tabIndex={0}
          aria-label="Upload a package folder"
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
            void handleFiles(() => collectDroppedFiles(items), "your folder");
          }}
          className={cn(
            "flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-5 text-center transition-colors",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            dragOver
              ? "border-primary bg-primary/10"
              : "border-border bg-card/40 hover:border-primary/40",
          )}
        >
          {busy ? (
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
          ) : (
            <FolderUp className="h-6 w-6 text-primary" />
          )}
          <p className="text-sm font-semibold text-foreground">Upload a folder</p>
          <p className="text-xs text-muted-foreground">
            Drop your package folder here or click to choose it. plugin.json,
            mcp.json and skills are read automatically.
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
                void handleFiles(() => Promise.resolve(collectPickedFiles(list)), "your folder");
              }
              e.currentTarget.value = "";
            }}
          />
        </div>

        {/* GitHub import */}
        <button
          type="button"
          onClick={onGithub}
          className={cn(
            "flex flex-col items-center gap-2 rounded-lg border border-border bg-card/40 p-5 text-center transition-colors",
            "hover:border-primary/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        >
          <Github className="h-6 w-6 text-primary" />
          <p className="text-sm font-semibold text-foreground">Import from GitHub</p>
          <p className="text-xs text-muted-foreground">
            Browse your public repositories, pick the package, and review it
            before anything is published.
          </p>
        </button>

        {/* Blank form */}
        <div className="flex flex-col items-center gap-2 rounded-lg border border-border bg-card/40 p-5 text-center">
          <PencilLine className="h-6 w-6 text-primary" />
          <p className="text-sm font-semibold text-foreground">Start from scratch</p>
          <p className="text-xs text-muted-foreground">
            Write it in the form — best for a single skill.
          </p>
          <div className="mt-1 flex items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => onBlank("skill")}>
              <FileText className="mr-1.5 h-3.5 w-3.5" />
              Skill
            </Button>
            <Button size="sm" variant="outline" onClick={() => onBlank("plugin")}>
              <Plug className="mr-1.5 h-3.5 w-3.5" />
              Plugin
            </Button>
          </div>
        </div>
      </div>

      {dropError && (
        <p className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {dropError}
        </p>
      )}

      <HowPublishingWorks />
    </section>
  );
}

/** The built-in author guide the page was missing: the four steps and the
 *  expected folder shape, inline, so nobody needs to find a doc first. */
function HowPublishingWorks() {
  const [open, setOpen] = useState(false);
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-foreground">
        How publishing works
      </p>
      <ol className="mt-2 grid gap-2 text-xs text-muted-foreground sm:grid-cols-4">
        <li className="flex gap-2">
          <StepBadge n={1} />
          Sign in with GitHub — it only proves who you are.
        </li>
        <li className="flex gap-2">
          <StepBadge n={2} />
          Add your package: drop the folder, import it from GitHub, or write it
          here.
        </li>
        <li className="flex gap-2">
          <StepBadge n={3} />
          Check it — the exact rules the store enforces run before you submit.
        </li>
        <li className="flex gap-2">
          <StepBadge n={4} />
          Publish. The checks re-run, it merges on green, and appears in the
          store within minutes.
        </li>
      </ol>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="mt-3 text-[11px] font-medium text-primary hover:underline"
      >
        {open ? "Hide the folder layout" : "What does a package folder look like?"}
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

— or, for a standalone skill, just one file:
my-skill/
└── SKILL.md                     ← starts with YAML frontmatter`}
          </pre>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Names are lowercase a-z 0-9 - . and the first published claim owns
            the name. No credentials in any file — tokens are placeholders like
            $plugin_…, filled in on the user's machine at connect time.
          </p>
        </div>
      )}
    </section>
  );
}

function StepBadge({ n }: { n: number }) {
  return (
    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full border border-primary/50 text-[10px] font-bold text-primary">
      {n}
    </span>
  );
}

// ---------------------------------------------------------------------------
// GitHub import: repos → candidates → prefilled draft
// ---------------------------------------------------------------------------

function GithubImportPanel({
  login,
  onBack,
  onImported,
}: {
  login: string;
  onBack: () => void;
  onImported: (draft: FolderDraft, origin: string) => void;
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
      setError(err instanceof Error ? err.message : "Could not list repositories.");
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
          cutOff
            ? "This repository is too large to scan fully, and no plugin.json or SKILL.md turned up in what GitHub returned — try a specific subfolder instead."
            : "No plugin.json and no SKILL.md in this repository — nothing here is a publishable package.",
        );
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not scan the repository.");
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
      setError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setBusy(null);
    }
  };

  const openRef = async () => {
    const parsed = parseRepoRef(refText);
    if (!parsed) {
      setError('Enter a repository as "owner/name" or paste its GitHub URL.');
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
      setError(err instanceof Error ? err.message : "Could not resolve the repository.");
      setBusy(null);
    }
  };

  return (
    <section className="space-y-4 rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
          Back
        </Button>
        <span className="text-xs font-semibold uppercase tracking-wider text-foreground">
          Import from GitHub
        </span>
        <span className="text-[11px] text-muted-foreground">
          public repositories only — you review everything before publishing
        </span>
      </div>

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-48 flex-1">
          <label className="mb-1 block text-xs font-medium text-foreground" htmlFor="gh-account">
            GitHub account
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
          List repositories
        </Button>
        <div className="min-w-48 flex-1">
          <label className="mb-1 block text-xs font-medium text-foreground" htmlFor="gh-ref">
            …or one specific repository
          </label>
          <input
            id="gh-ref"
            value={refText}
            onChange={(e) => setRefText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void openRef();
            }}
            placeholder="owner/repo or a GitHub URL"
            className={inputCls(false)}
          />
        </div>
        <Button size="sm" variant="outline" onClick={() => void openRef()} disabled={busy !== null}>
          {busy?.startsWith("resolve:") ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : null}
          Scan
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
                  <span className="truncate text-[11px] text-muted-foreground">{r.description}</span>
                )}
              </button>
            </li>
          ))}
          {repos.length === 0 && (
            <li className="px-2 py-1.5 text-xs text-muted-foreground">
              This account has no public repositories.
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
              ← all repositories
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
              GitHub cut this listing short — this repository is large enough
              that the scan may have missed a package outside what is shown
              here.
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
                    {c.kind === "plugin" ? "Plugin package" : "Skill"}
                  </span>
                  <span className="font-mono text-[11px] text-muted-foreground">
                    /{c.dir || ""}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    {c.paths.length} file{c.paths.length === 1 ? "" : "s"}
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
                    Review &amp; prefill
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
// The form (prefilled or blank) + live store-card preview
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
}) {
  const errorFor = (field: string) => errors.filter((e) => e.field === field);
  const generalErrors = errors.filter((e) => e.field === null);

  return (
    <section className="space-y-4 rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-foreground">
          What you are publishing
        </span>
        <div className="ml-auto flex items-center gap-1">
          <KindButton
            active={draft.kind === "skill"}
            icon={<FileText className="h-3.5 w-3.5" />}
            label="Skill"
            onClick={() => set({ kind: "skill" })}
          />
          <KindButton
            active={draft.kind === "plugin"}
            icon={<Plug className="h-3.5 w-3.5" />}
            label="Connector plugin"
            onClick={() => set({ kind: "plugin" })}
          />
        </div>
      </div>

      {prefill && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2">
          <p className="text-xs text-foreground">
            <Check className="mr-1 inline h-3.5 w-3.5 text-primary" />
            Loaded from <span className="font-medium">{prefill.origin}</span> —
            review the fields below, then check and publish.
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
          label="Package name"
          hint="a-z 0-9 - . — first published claim owns the name"
          errors={errorFor("name")}
        >
          <input
            value={draft.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="my-skill"
            className={inputCls(errorFor("name").length > 0)}
          />
        </Field>
        <Field label="Version" hint="SemVer, e.g. 1.0.0" errors={errorFor("version")}>
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
          <Field label="Title" errors={errorFor("title")}>
            <input
              value={draft.title}
              onChange={(e) => set({ title: e.target.value })}
              placeholder="My Skill"
              className={inputCls(errorFor("title").length > 0)}
            />
          </Field>
          <Field
            label="Description"
            hint={`${draft.description.length}/500 — what the skill does, one or two sentences`}
            errors={errorFor("description")}
          >
            <textarea
              value={draft.description}
              onChange={(e) => set({ description: e.target.value })}
              rows={2}
              className={inputCls(errorFor("description").length > 0)}
            />
          </Field>
          <Field label="Categories" hint="comma-separated, optional" errors={[]}>
            <input
              value={draft.categories}
              onChange={(e) => set({ categories: e.target.value })}
              placeholder="writing, research"
              className={inputCls(false)}
            />
          </Field>
          <Field
            label="SKILL.md"
            hint={`${(new TextEncoder().encode(draft.skill_md).length / 1024).toFixed(1)} KB / ${MAX_SKILL_MD_BYTES / 1024} KB — the instructions the assistant follows, starts with YAML frontmatter`}
            errors={errorFor("skill_md")}
            action={
              draft.skill_md.trim() === "" ? (
                <button
                  type="button"
                  onClick={() => set({ skill_md: SKILL_TEMPLATE })}
                  className="text-[11px] font-medium text-primary hover:underline"
                >
                  Insert template
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
            hint="the Agent Plugins manifest (name, description, version, extensions)"
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
            hint='optional — exactly one server, with its "type" ("streamable-http" or "stdio"): an https URL, or a pinned npx/uvx/docker command'
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
            label="Bundled skills"
            hint="instructions that install together with the plugin — up to 10"
            errors={errorFor("skills")}
          >
            {draft.skills.length === 0 ? (
              <p className="rounded-md border border-dashed border-border px-2.5 py-2 text-[11px] text-muted-foreground">
                None. Skills travel in the package folder as
                skills/&lt;name&gt;/SKILL.md — upload a folder or import from
                GitHub to bundle them.
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
                      aria-label={`Remove bundled skill ${s.name}`}
                      onClick={() => set({ skills: draft.skills.filter((x) => x.name !== s.name) })}
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
            label="Usage card"
            hint="optional — keywords that tell the assistant when the plugin is relevant"
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

      <CardPreview draft={draft} />

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
        <p className="flex items-center gap-2 text-xs text-primary">
          <Check className="h-3.5 w-3.5" /> Passes every rule the store checks —
          ready to publish.
        </p>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/50 pt-3">
        <Button size="sm" variant="ghost" onClick={onStartOver}>
          <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
          Start over
        </Button>
        <Button size="sm" variant="outline" onClick={onCheck} disabled={validating}>
          {validating ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
          Check
        </Button>
        <Button
          size="sm"
          onClick={onPublish}
          disabled={submitting || !signedIn}
          title={signedIn ? "Publish under your GitHub name" : "Sign in with GitHub first"}
        >
          {submitting ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
          )}
          Publish
        </Button>
      </div>
    </section>
  );
}

/** What the store card will roughly look like — the author sees the listing
 *  before the world does. Derived from the draft on every keystroke. */
function CardPreview({ draft }: { draft: Draft }) {
  const preview = useMemo(() => {
    if (draft.kind === "skill") {
      return {
        title: draft.title.trim() || draft.name.trim() || "Untitled skill",
        description: draft.description.trim() || "No description yet.",
        badge: "SKILL",
        detail: draft.categories
          .split(",")
          .map((c) => c.trim())
          .filter(Boolean)
          .join(" · "),
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
    if (draft.skills.length > 0)
      bits.push(`${draft.skills.length} bundled skill${draft.skills.length === 1 ? "" : "s"}`);
    if (server) bits.push(server);
    return {
      title: displayName || "Untitled plugin",
      description: description || "No description yet.",
      badge: "PLUGIN",
      detail: bits.join(" · "),
    };
  }, [draft]);

  return (
    <div>
      <p className="mb-1 text-xs font-medium text-foreground">Store card preview</p>
      <div className="rounded-lg border border-border bg-background p-3">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-foreground">{preview.title}</span>
          <span className="rounded-full border border-primary/40 px-1.5 py-px text-[9px] font-bold tracking-wider text-primary">
            {preview.badge}
          </span>
          <span className="rounded-full border border-border px-1.5 py-px text-[9px] font-semibold tracking-wider text-muted-foreground">
            UNREVIEWED
          </span>
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            v{draft.version.trim() || "?"}
          </span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">{preview.description}</p>
        {preview.detail && (
          <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
            {preview.detail}
          </p>
        )}
      </div>
    </div>
  );
}

function inputCls(hasError: boolean): string {
  return cn(
    "w-full rounded-md border bg-background px-2.5 py-1.5 text-sm text-foreground",
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

/** After the PR is open: link it, then watch the live feed until the package
 *  actually appears — "published" only means anything once the store shows it. */
function PublishedCard({
  result,
  onPublishAnother,
}: {
  result: SubmitResultWire;
  onPublishAnother: () => void;
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
    <div className="space-y-4">
      <section className="rounded-lg border border-primary/30 bg-primary/5 p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Check className="h-4 w-4 text-primary" />
          Submitted: {result.name} v{result.version}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          The registry received your package and opened its pull request. The
          automated checks now run; when they pass, it merges and appears in
          the store on its own.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {result.pr_url && (
            <Button size="sm" variant="outline" onClick={() => openExternalUrl(result.pr_url ?? "")}>
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              View the pull request
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onPublishAnother}>
            Publish another
          </Button>
        </div>
      </section>

      <section className="space-y-4 rounded-lg border border-border bg-card/40 p-4">
        {live ? (
          <p className="flex items-center gap-2 text-sm text-foreground">
            <Check className="h-4 w-4 text-primary" />
            Live — {result.name} v{result.version} is in the community store
            now. Find it under Plugins → Community.
          </p>
        ) : waitedOut ? (
          <p className="text-xs text-muted-foreground">
            Still not in the feed after several minutes — open the pull request
            above to see what its checks say.
          </p>
        ) : (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Watching the live feed until your package appears — usually a few
            minutes.
          </p>
        )}

        {/* Gated on `live` on purpose: handed out a minute too early, the
            command resolves against an index that does not list the entry yet
            and fails for whoever the author sent it to. */}
        {live && result.install && (
          <InstallStandard
            install={result.install}
            heading="Share it"
            note="Anyone can run this now — it resolves your listing from the public registry."
          />
        )}
      </section>
    </div>
  );
}
