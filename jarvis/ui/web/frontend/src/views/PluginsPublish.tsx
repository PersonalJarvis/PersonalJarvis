import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Copy,
  ExternalLink,
  FileText,
  Loader2,
  LogOut,
  Plug,
  UploadCloud,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";

// ---------------------------------------------------------------------------
// Publish tab — the in-app half of the marketplace's publish path.
//
// Anyone may publish: whatever passes the automated checks goes live with no
// human review, so the copy on this page is honest about the two things that
// matter — the upload appears PUBLICLY under the user's GitHub name, and the
// only gate is the rule check. Identity comes from the GitHub device flow
// (the app never sees a password and holds no client secret); the submission
// travels through the same storefront endpoint the web form uses, so web and
// app cannot drift apart.
// ---------------------------------------------------------------------------

interface IdentityWire {
  enabled: boolean;
  signed_in: boolean;
  login?: string;
  avatar_url?: string | null;
  unreachable?: string;
}

interface SigninStartWire {
  flow_id: string;
  user_code: string;
  verification_uri?: string | null;
  verification_uri_complete?: string | null;
  interval?: number;
}

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
}

type Kind = "skill" | "plugin";

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
};

const SKILL_TEMPLATE = `---
name: my-skill
description: One sentence on when the assistant should reach for this skill.
version: 1.0.0
---

# My Skill

Write the instructions the assistant follows, step by step.
`;

async function fetchIdentity(): Promise<IdentityWire> {
  const res = await fetch("/api/marketplace/publish/identity", { cache: "no-store" });
  if (!res.ok) throw new Error(`Identity request failed (${res.status})`);
  return res.json();
}

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
  return {
    body: {
      ...base,
      plugin_json: pluginJson,
      mcp_json: mcpJson,
      usage_card: draft.usage_card.trim() ? draft.usage_card : null,
    },
  };
}

export function PublishTab() {
  const queryClient = useQueryClient();
  const identity = useQuery({ queryKey: ["marketplace-publish-identity"], queryFn: fetchIdentity });

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [errors, setErrors] = useState<FieldErrorWire[]>([]);
  const [checked, setChecked] = useState(false);
  const [result, setResult] = useState<SubmitResultWire | null>(null);

  const set = (patch: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...patch }));
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

  const errorFor = (field: string) => errors.filter((e) => e.field === field);
  const generalErrors = errors.filter((e) => e.field === null);

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
          setDraft(EMPTY_DRAFT);
          setErrors([]);
          setChecked(false);
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
        <p className="text-xs text-muted-foreground">
          Anyone can publish. Whatever passes the automated checks goes live
          without human review, publicly under your GitHub name — installing
          users see exactly what your package would be allowed to do before
          they accept it.
        </p>
      </header>

      <SignInCard identity={identity.data} loading={identity.isLoading} />

      <section className="space-y-4 rounded-lg border border-border bg-card/40 p-4">
        <div className="flex items-center gap-2">
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
              hint="the instructions the assistant follows — starts with YAML frontmatter"
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
              hint="optional — exactly one server: an https URL, or a pinned npx/uvx/docker command"
              errors={errorFor("mcp_json")}
            >
              <textarea
                value={draft.mcp_json_text}
                onChange={(e) => set({ mcp_json_text: e.target.value })}
                rows={6}
                spellCheck={false}
                placeholder='{ "mcpServers": { "my-plugin": { "url": "https://…" } } }'
                className={cn(inputCls(errorFor("mcp_json").length > 0), "font-mono text-xs")}
              />
            </Field>
            <Field label="Usage card" hint="optional — keywords that tell the assistant when the plugin is relevant" errors={[]}>
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
          <p className="flex items-center gap-2 text-xs text-primary">
            <Check className="h-3.5 w-3.5" /> Passes every rule the store
            checks — ready to publish.
          </p>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border/50 pt-3">
          <Button
            size="sm"
            variant="outline"
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
          >
            {validateMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Check
          </Button>
          <Button
            size="sm"
            onClick={() => submitMutation.mutate()}
            disabled={submitMutation.isPending || !identity.data?.signed_in}
            title={
              identity.data?.signed_in
                ? "Publish under your GitHub name"
                : "Sign in with GitHub first"
            }
          >
            {submitMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <UploadCloud className="mr-1.5 h-3.5 w-3.5" />
            )}
            Publish
          </Button>
        </div>
      </section>
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

/** GitHub device-flow sign-in: show the short code, the user types it at
 *  github.com/login/device, we poll until approved. No password, no secret. */
function SignInCard({
  identity,
  loading,
}: {
  identity: IdentityWire | undefined;
  loading: boolean;
}) {
  const queryClient = useQueryClient();
  const [flow, setFlow] = useState<SigninStartWire | null>(null);
  const [flowError, setFlowError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const startMutation = useMutation({
    mutationFn: async (): Promise<SigninStartWire> => {
      const res = await fetch("/api/marketplace/publish/signin/start", { method: "POST" });
      if (!res.ok) {
        const detail = await res
          .json()
          .then((b: { detail?: string }) => b.detail)
          .catch(() => undefined);
        throw new Error(detail ?? `Sign-in start failed (${res.status})`);
      }
      return res.json();
    },
    onSuccess: (f) => {
      setFlow(f);
      setFlowError(null);
    },
  });

  const signOutMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch("/api/marketplace/publish/identity", { method: "DELETE" });
      if (!res.ok) throw new Error(`Sign-out failed (${res.status})`);
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["marketplace-publish-identity"] });
    },
  });

  // Poll the running flow until GitHub reports approval or an error.
  useEffect(() => {
    if (!flow) return;
    const intervalMs = Math.max(2, flow.interval ?? 5) * 1000;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/marketplace/publish/signin/poll/${flow.flow_id}`, {
          cache: "no-store",
        });
        if (res.status === 404) {
          setFlow(null);
          return;
        }
        const data = (await res.json()) as { status: string; error?: string };
        if (data.status === "connected") {
          setFlow(null);
          queryClient.invalidateQueries({ queryKey: ["marketplace-publish-identity"] });
        } else if (data.status === "error") {
          setFlow(null);
          setFlowError(data.error ?? "sign-in failed");
        }
      } catch {
        // Network blip — keep polling until the flow expires.
      }
    }, intervalMs);
    return () => clearInterval(timer);
  }, [flow, queryClient]);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  const signedIn = identity?.signed_in === true;
  return (
    <section className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-foreground">
            Identity
          </p>
          <p className="text-xs text-muted-foreground">
            {signedIn ? (
              <>
                Signed in as{" "}
                <span className="font-medium text-foreground">@{identity?.login}</span> — your
                packages publish under this name.
              </>
            ) : (
              "Sign in with GitHub to publish. The sign-in only proves who you are — it grants nothing on your account."
            )}
          </p>
          {identity?.unreachable && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              GitHub is unreachable right now — your sign-in state could not be
              checked.
            </p>
          )}
        </div>
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : signedIn ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => signOutMutation.mutate()}
            disabled={signOutMutation.isPending}
          >
            <LogOut className="mr-1.5 h-3.5 w-3.5" />
            Sign out
          </Button>
        ) : flow ? null : (
          <Button
            size="sm"
            onClick={() => startMutation.mutate()}
            disabled={startMutation.isPending}
          >
            {startMutation.isPending ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Sign in with GitHub
          </Button>
        )}
      </div>

      {flow && (
        <div className="mt-3 rounded-md border border-border bg-muted/30 p-3">
          <p className="text-xs text-muted-foreground">
            Enter this code at{" "}
            <button
              type="button"
              onClick={() => openExternalUrl(flow.verification_uri ?? "https://github.com/login/device")}
              className="font-medium text-primary hover:underline"
            >
              github.com/login/device
              <ExternalLink className="ml-0.5 inline h-3 w-3" />
            </button>
            {" "}— waiting for your approval…
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="rounded-md border border-border bg-background px-3 py-1.5 font-mono text-lg font-semibold tracking-[0.25em] text-foreground">
              {flow.user_code}
            </code>
            <button
              type="button"
              onClick={async () => {
                if (await robustCopy(flow.user_code)) setCopied(true);
              }}
              className="grid h-7 w-7 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title={copied ? "Copied" : "Copy code"}
              aria-label="Copy the sign-in code"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-primary" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </button>
            <Loader2 className="ml-auto h-3.5 w-3.5 animate-spin text-muted-foreground" />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                // Tell the backend to drop the flow, then forget it locally
                // either way — an already-finished flow 404s harmlessly.
                void fetch(`/api/marketplace/publish/signin/${flow.flow_id}`, {
                  method: "DELETE",
                }).catch(() => undefined);
                setFlow(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
      {flowError && (
        <p className="mt-2 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {flowError}
        </p>
      )}
    </section>
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

      <section className="rounded-lg border border-border bg-card/40 p-4">
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
      </section>
    </div>
  );
}
