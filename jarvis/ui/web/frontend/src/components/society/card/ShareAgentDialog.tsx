/**
 * Share an agent: its design as a template others can install.
 *
 * Left: the public version — the store-card summary, the marketplace name and
 * version, and the privacy check (what was taken out, what never travels).
 * Right: the template itself as a code block, ready to copy or export. Bottom:
 * publish to the marketplace under the person's GitHub name.
 *
 * The server builds the template (jarvis/society/agent_template.py); every
 * edit here is saved and the code block shows the server's answer, so what
 * the person reads is byte for byte what Publish files. "Let <agent> prepare
 * it" sends the agent a chat message; the agent writes its own public version
 * with the society_share_template tool and this sheet picks it up.
 */
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  Copy,
  Download,
  ExternalLink,
  FileJson,
  Github,
  Loader2,
  Lock,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  GithubSignInDialog,
  PublisherAvatar,
  usePublishIdentity,
} from "@/components/marketplace/PublishIdentity";
import { fill, useLocaleChunk, useT } from "@/i18n";
import {
  MARKETPLACE_ISSUE_FORM_URL,
  ShareError,
  downloadTemplate,
  fetchLiveStatus,
  fetchShareDraft,
  publishShareDraft,
  saveShareDraft,
  shareDraftKey,
  templateFileName,
  type PublishShareResult,
  type ShareDraftEdits,
  type ShareDraftWire,
  type ShareFinding,
} from "@/lib/agentShare";
import { robustCopy } from "@/lib/clipboard";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";

import { AgentSwatch } from "../AgentSwatch";
import { useSocietyChatStore } from "../chat/AgentChatPanel";
import type { SocietyAgent } from "../data";

const CodeBlock = lazy(() =>
  import("@/components/docs/CodeBlock").then((m) => ({ default: m.CodeBlock })),
);

/** How long the sheet waits for the agent to write its public version. */
const POLISH_TIMEOUT_MS = 4 * 60 * 1000;
const SAVE_DEBOUNCE_MS = 600;

interface FormState {
  summary: string;
  listingName: string;
  version: string;
  categories: string;
}

function formFrom(draft: ShareDraftWire): FormState {
  return {
    summary: draft.listing.description,
    listingName: draft.listing.name,
    version: draft.listing.version,
    categories: draft.listing.categories.join(", "),
  };
}

function editsFrom(form: FormState): ShareDraftEdits {
  return {
    summary: form.summary,
    listing_name: form.listingName.trim().toLowerCase(),
    version: form.version.trim(),
    categories: form.categories
      .split(",")
      .map((c) => c.trim().toLowerCase())
      .filter(Boolean)
      .slice(0, 10),
  };
}

export function ShareAgentDialog({ agent, onClose }: { agent: SocietyAgent; onClose: () => void }) {
  const t = useT();
  useLocaleChunk("society");
  useLocaleChunk("marketplace");
  const client = useQueryClient();
  const key = shareDraftKey(agent.agentId);
  const [waitingSince, setWaitingSince] = useState<number | null>(null);
  const draftQuery = useQuery({
    queryKey: key,
    queryFn: () => fetchShareDraft(agent.agentId),
    // While the agent writes its public version, look for it every few seconds.
    refetchInterval: waitingSince !== null ? 2500 : false,
  });
  const draft = draftQuery.data;
  const [form, setForm] = useState<FormState | null>(null);
  const dirty = useRef(false);
  const polishedBefore = useRef<string | null>(null);

  // The form follows the server until the person starts typing.
  useEffect(() => {
    if (draft && (!dirty.current || form === null)) setForm(formFrom(draft));
  }, [draft]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (edits: ShareDraftEdits) => saveShareDraft(agent.agentId, edits),
    onSuccess: (fresh) => {
      dirty.current = false;
      client.setQueryData(key, fresh);
    },
  });

  // Debounced save: the code block always shows what the server built.
  useEffect(() => {
    if (!form || !dirty.current) return;
    const timer = window.setTimeout(() => save.mutate(editsFrom(form)), SAVE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [form]); // eslint-disable-line react-hooks/exhaustive-deps

  // The agent finished its public version (or gave up): stop waiting.
  useEffect(() => {
    if (waitingSince === null || !draft) return;
    const signature = `${draft.polished_by}|${draft.listing.description}|${draft.template.instructions.length}`;
    if (polishedBefore.current !== null && signature !== polishedBefore.current) {
      dirty.current = false;
      setForm(formFrom(draft));
      setWaitingSince(null);
    } else if (Date.now() - waitingSince > POLISH_TIMEOUT_MS) {
      setWaitingSince(null);
    }
  }, [draft, waitingSince]);

  const edit = (patch: Partial<FormState>) => {
    dirty.current = true;
    setForm((current) => (current ? { ...current, ...patch } : current));
  };

  const json = useMemo(
    () => (draft ? JSON.stringify(draft.submission, null, 2) : ""),
    [draft],
  );

  return (
    <Dialog.Root open onOpenChange={(open) => (open ? undefined : onClose())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/60 backdrop-blur-sm" />
        <Dialog.Content
          data-testid="share-agent-dialog"
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[92dvh] w-[min(1040px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-popover text-foreground shadow-float focus:outline-none"
        >
          <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-4">
            <AgentSwatch agent={agent} size={40} />
            <div className="min-w-0 flex-1">
              <Dialog.Title className="truncate font-display text-base font-semibold tracking-tight">
                {fill(t("society.share.title"), { agent: agent.name })}
              </Dialog.Title>
              <Dialog.Description className="text-xs text-muted-foreground">
                {t("society.share.subtitle")}
              </Dialog.Description>
            </div>
            <button
              type="button"
              aria-label={t("society.card.close")}
              onClick={onClose}
              className="rounded-md p-2 text-muted-foreground hover:bg-secondary hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </header>

          {draftQuery.isLoading && (
            <p className="flex items-center gap-2 px-5 py-10 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t("society.share.loading")}
            </p>
          )}
          {draftQuery.error && (
            <p role="alert" className="flex items-center gap-2 px-5 py-10 text-sm text-destructive">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {(draftQuery.error as Error).message}
            </p>
          )}

          {draft && form && (
            <>
              <div className="grid min-h-0 flex-1 overflow-y-auto md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] md:overflow-hidden">
                <section className="min-h-0 space-y-5 px-5 py-4 md:overflow-y-auto" aria-label={t("society.share.public_version")}>
                  <PolishBar
                    agent={agent}
                    draft={draft}
                    waiting={waitingSince !== null}
                    onSent={() => {
                      polishedBefore.current = `${draft.polished_by}|${draft.listing.description}|${draft.template.instructions.length}`;
                      setWaitingSince(Date.now());
                    }}
                    onReset={() => {
                      dirty.current = false;
                      save.mutate({ reset: true });
                    }}
                  />

                  <label className="block">
                    <span className="mb-1 flex items-baseline justify-between text-xs font-medium text-foreground">
                      {t("society.share.summary")}
                      <span className="tabular-nums text-muted-foreground">{form.summary.length}/500</span>
                    </span>
                    <textarea
                      value={form.summary}
                      maxLength={500}
                      rows={3}
                      onChange={(e) => edit({ summary: e.target.value })}
                      placeholder={t("society.share.summary_placeholder")}
                      data-testid="share-summary"
                      className="w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground outline-none placeholder:text-faint-foreground focus:border-border-strong"
                    />
                  </label>

                  <div className="grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
                    <label className="block min-w-0">
                      <span className="mb-1 block text-xs font-medium text-foreground">{t("society.share.listing_name")}</span>
                      <input
                        value={form.listingName}
                        onChange={(e) => edit({ listingName: e.target.value })}
                        spellCheck={false}
                        className="h-9 w-full rounded-md border border-border bg-background px-3 font-mono text-xs text-foreground outline-none focus:border-border-strong"
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-xs font-medium text-foreground">{t("society.share.version")}</span>
                      <input
                        value={form.version}
                        onChange={(e) => edit({ version: e.target.value })}
                        spellCheck={false}
                        className="h-9 w-full rounded-md border border-border bg-background px-3 font-mono text-xs tabular-nums text-foreground outline-none focus:border-border-strong"
                      />
                    </label>
                  </div>

                  <label className="block">
                    <span className="mb-1 block text-xs font-medium text-foreground">{t("society.share.categories")}</span>
                    <input
                      value={form.categories}
                      onChange={(e) => edit({ categories: e.target.value })}
                      placeholder={t("society.share.categories_placeholder")}
                      className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none placeholder:text-faint-foreground focus:border-border-strong"
                    />
                  </label>

                  <PrivacyCheck findings={draft.findings} />
                </section>

                <section className="flex min-h-[18rem] flex-col border-t border-border bg-background/40 px-5 py-4 md:min-h-0 md:border-l md:border-t-0" aria-label={t("society.share.template")}>
                  <TemplateCode
                    fileName={templateFileName(draft.listing)}
                    json={json}
                    saving={save.isPending}
                  />
                </section>
              </div>

              <PublishBar agentId={agent.agentId} draft={draft} json={json} saving={save.isPending || dirty.current} />
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** "Let <agent> prepare it": the agent writes its own public version. */
function PolishBar({
  agent,
  draft,
  waiting,
  onSent,
  onReset,
}: {
  agent: SocietyAgent;
  draft: ShareDraftWire;
  waiting: boolean;
  onSent: () => void;
  onReset: () => void;
}) {
  const t = useT();
  // The card's chat column runs on the society store; the message lands in
  // the conversation the person sees right beside this sheet.
  const activeSessionId = useSocietyChatStore((s) => s.activeSessionId);
  const send = useSocietyChatStore((s) => s.send);
  const [error, setError] = useState<string | null>(null);
  const chatReady = Boolean(agent.chatSessionId) && activeSessionId === agent.chatSessionId;

  const ask = async () => {
    setError(null);
    const result = await send(t("society.share.polish_prompt"));
    if (result === "failed" || result === "stale") setError(t("society.share.polish_failed"));
    else onSent();
  };

  return (
    <div className="rounded-lg border border-border bg-card px-3 py-3">
      <div className="flex items-start gap-3">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">
            {draft.polished_by
              ? fill(t("society.share.polished_by"), { agent: draft.polished_by })
              : fill(t("society.share.polish_title"), { agent: agent.name })}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {waiting
              ? fill(t("society.share.polish_waiting"), { agent: agent.name })
              : chatReady
                ? fill(t("society.share.polish_hint"), { agent: agent.name })
                : t("society.share.polish_unavailable")}
          </p>
          {error && <p role="alert" className="mt-1 text-xs text-destructive">{error}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button size="sm" variant="outline" onClick={() => void ask()} disabled={!chatReady || waiting} data-testid="share-polish">
            {waiting ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Sparkles className="mr-1.5 h-3.5 w-3.5" />}
            {fill(t("society.share.polish_cta"), { agent: agent.name })}
          </Button>
          {draft.polished_by && !waiting && (
            <button type="button" onClick={onReset} className="text-micro text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">
              {t("society.share.reset")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const FINDING_ORDER: ShareFinding["kind"][] = ["secret", "email", "phone", "path", "address"];

/** What was taken out, and what never travels at all. */
function PrivacyCheck({ findings }: { findings: ShareFinding[] }) {
  const t = useT();
  const sorted = [...findings].sort(
    (a, b) => FINDING_ORDER.indexOf(a.kind) - FINDING_ORDER.indexOf(b.kind),
  );
  return (
    <div className="space-y-3" data-testid="share-privacy">
      <div className="rounded-lg border border-border bg-card px-3 py-3">
        <p className="flex items-center gap-2 text-sm font-medium text-foreground">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          {sorted.length === 0
            ? t("society.share.privacy_clean")
            : fill(t("society.share.privacy_removed"), { count: sorted.length })}
        </p>
        {sorted.length === 0 ? (
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t("society.share.privacy_clean_hint")}</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {sorted.map((finding, index) => (
              <li key={`${finding.kind}-${finding.hint}-${index}`} className="flex items-baseline gap-2 text-xs text-muted-foreground">
                <span className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-micro font-medium text-foreground">
                  {t(`society.share.finding_${finding.kind}`)}
                </span>
                <span className="min-w-0 truncate">
                  {fill(t("society.share.finding_where"), {
                    hint: finding.hint,
                    field: t(`society.share.field_${finding.field}`),
                  })}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="rounded-lg bg-secondary px-3 py-2.5">
        <p className="flex items-center gap-2 text-xs font-medium text-foreground">
          <Lock className="h-3.5 w-3.5 text-muted-foreground" />
          {t("society.share.never_title")}
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{t("society.share.never_body")}</p>
      </div>
    </div>
  );
}

/** The template as the code block people copy, export, or read before publishing. */
function TemplateCode({ fileName, json, saving }: { fileName: string; json: string; saving: boolean }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const copy = () =>
    void robustCopy(json).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-muted/40" data-testid="share-template-code">
      <div className="flex shrink-0 items-center gap-2 border-b border-border/60 bg-muted/30 px-3 py-1.5">
        <FileJson className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">{fileName}</span>
        {saving && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-label={t("society.card.saving")} />}
        <button type="button" onClick={copy} className="flex items-center gap-1 rounded px-1.5 py-1 text-micro text-muted-foreground hover:bg-muted hover:text-foreground">
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? t("society.share.copied") : t("society.share.copy")}
        </button>
        <button type="button" onClick={() => downloadTemplate(fileName, json)} className="flex items-center gap-1 rounded px-1.5 py-1 text-micro text-muted-foreground hover:bg-muted hover:text-foreground" data-testid="share-export">
          <Download className="h-3 w-3" />
          {t("society.share.export")}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <Suspense fallback={<pre className="m-0 px-3 py-2 text-xs text-foreground/90"><code>{json}</code></pre>}>
          <CodeBlock language="json" code={json} chrome={false} />
        </Suspense>
      </div>
    </div>
  );
}

/** Sign in, publish, and watch it go live. */
function PublishBar({
  agentId,
  draft,
  json,
  saving,
}: {
  agentId: string;
  draft: ShareDraftWire;
  json: string;
  saving: boolean;
}) {
  const t = useT();
  const client = useQueryClient();
  const identity = usePublishIdentity();
  const [signInOpen, setSignInOpen] = useState(false);
  const [result, setResult] = useState<PublishShareResult | null>(null);
  const [live, setLive] = useState(false);
  const signedIn = Boolean(identity.data?.signed_in);
  const enabled = identity.data?.enabled !== false;

  const publish = useMutation({
    mutationFn: () => publishShareDraft(agentId),
    onSuccess: (done) => {
      setResult(done);
      void client.invalidateQueries({ queryKey: shareDraftKey(agentId) });
      void client.invalidateQueries({ queryKey: ["marketplace-community"] });
    },
  });

  // Watch the feed until the published version is live (or the sheet closes).
  useEffect(() => {
    if (!result || live) return;
    let cancelled = false;
    const tick = async () => {
      const isLive = await fetchLiveStatus(result.name, result.version).catch(() => false);
      if (!cancelled && isLive) {
        setLive(true);
        void client.invalidateQueries({ queryKey: ["marketplace-community"] });
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [result, live, client]);

  const failure = publish.error instanceof ShareError ? publish.error : null;
  const browserFallback = failure?.field === "browser_fallback";
  const fileByHand = () => {
    void robustCopy(json);
    openExternalUrl(MARKETPLACE_ISSUE_FORM_URL);
  };

  if (result) {
    const url = result.issue_url ?? result.pr_url ?? result.url ?? null;
    return (
      <footer className="shrink-0 space-y-2 border-t border-border bg-card px-5 py-3" data-testid="share-published">
        <div className="flex flex-wrap items-center gap-3">
          <span className={cn("grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-secondary", live ? "text-foreground-strong" : "text-foreground")}>
            {live ? <Check className="h-4 w-4" /> : <Loader2 className="h-4 w-4 animate-spin" />}
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-foreground">
              {live
                ? fill(t("society.share.live_title"), { entry: result.name, version: result.version })
                : fill(t("society.share.submitted_title"), { entry: result.name, version: result.version })}
            </p>
            <p className="text-xs text-muted-foreground">
              {live ? t("society.share.live_body") : t("society.share.submitted_body")}
            </p>
          </div>
          {url && (
            <Button size="sm" variant="outline" onClick={() => openExternalUrl(url)}>
              <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
              {t("society.share.view_submission")}
            </Button>
          )}
        </div>
        {result.install?.cli && (
          <code className="block rounded-md border border-border bg-muted px-2.5 py-1.5 font-mono text-xs text-foreground">
            {result.install.cli}
          </code>
        )}
      </footer>
    );
  }

  return (
    <footer className="shrink-0 space-y-2 border-t border-border bg-card px-5 py-3">
      {draft.errors.length > 0 && (
        <p role="alert" className="flex items-start gap-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {draft.errors[0]}
        </p>
      )}
      {failure && (
        <p role="alert" className="flex items-start gap-2 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {browserFallback ? t("society.share.fallback_hint") : failure.message}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <p className="min-w-0 flex-1 text-xs leading-relaxed text-muted-foreground">
          {draft.published
            ? fill(t("society.share.published_before"), { version: draft.published.version })
            : t("society.share.publish_hint")}
        </p>
        {browserFallback && (
          <Button size="sm" variant="outline" onClick={fileByHand}>
            <Github className="mr-1.5 h-3.5 w-3.5" />
            {t("society.share.fallback_cta")}
          </Button>
        )}
        {!enabled ? (
          <span className="text-xs text-muted-foreground">{t("society.share.publish_disabled")}</span>
        ) : !signedIn ? (
          <Button size="sm" onClick={() => setSignInOpen(true)} disabled={identity.isLoading} data-testid="share-sign-in">
            <Github className="mr-1.5 h-3.5 w-3.5" />
            {t("society.share.sign_in")}
          </Button>
        ) : (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <PublisherAvatar login={identity.data?.login} url={identity.data?.avatar_url} size={20} />
              @{identity.data?.login}
            </span>
            <Button
              size="sm"
              onClick={() => publish.mutate()}
              disabled={publish.isPending || saving || draft.errors.length > 0}
              data-testid="share-publish"
            >
              {publish.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <UploadCloud className="mr-1.5 h-3.5 w-3.5" />}
              {t("society.share.publish")}
            </Button>
          </div>
        )}
      </div>
      {signInOpen && <GithubSignInDialog onClose={() => setSignInOpen(false)} />}
    </footer>
  );
}

export default ShareAgentDialog;
