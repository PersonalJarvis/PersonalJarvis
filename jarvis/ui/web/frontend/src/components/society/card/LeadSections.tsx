/**
 * The two spec-sheet sections that are different for Jarvis, because Jarvis
 * is the harness itself (maintainer, 2026-09-02):
 *
 *  - Brain: the provider/model the front page's chat and the voice stage use
 *    right now (the shared "jarvis" store's draft), not a per-row field.
 *  - Standing instructions: the person's agent-instructions file (the same
 *    one the Agent Instructions section edits), editable in place and
 *    applied on the next turn — no restart.
 */
import { useEffect, useState } from "react";
import { Pencil } from "lucide-react";

import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useAgentChatStore } from "@/store/agentChat";

interface InstructionsPayload {
  content: string;
  exists: boolean;
  filename: string;
  template: string;
}

export function LeadBrain() {
  const t = useT();
  const draft = useAgentChatStore((s) => s.draft);
  const providerById = useAgentChatStore((s) => s.providerById);
  const loadCatalog = useAgentChatStore((s) => s.loadCatalog);
  const catalog = useAgentChatStore((s) => s.catalog);
  useEffect(() => {
    if (!catalog) void loadCatalog();
  }, [catalog, loadCatalog]);
  const provider = providerById(draft.provider);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {provider ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-0.5 text-[12px] text-foreground">
            <ProviderLogo providerId={provider.id} label={provider.label} size="sm" />
            {provider.label}
          </span>
        ) : (
          <Badge variant="outline">{t("society.card.default_brain")}</Badge>
        )}
        <Badge variant="secondary" className="font-mono text-[11px]">
          {draft.model || t("society.chat.model_provider_default")}
        </Badge>
        {draft.effort ? <Badge variant="outline">{draft.effort}</Badge> : null}
        <Badge variant="outline">{t("society.tier.lead")}</Badge>
      </div>
      <p className="text-[11px] text-muted-foreground">{t("society.card.brain_shared_hint")}</p>
    </div>
  );
}

export function LeadInstructions() {
  const t = useT();
  const [payload, setPayload] = useState<InstructionsPayload | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void fetch("/api/settings/agent-instructions")
      .then((r) => (r.ok ? (r.json() as Promise<InstructionsPayload>) : null))
      .then((data) => {
        if (live && data) setPayload(data);
      })
      .catch(() => {
        /* the section below says the file could not be read */
      });
    return () => {
      live = false;
    };
  }, []);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch("/api/settings/agent-instructions", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: draft }),
      });
      if (!res.ok) {
        const detail = (await res.json().catch(() => null)) as { detail?: unknown } | null;
        throw new Error(typeof detail?.detail === "string" ? detail.detail : `save ${res.status}`);
      }
      setPayload((await res.json()) as InstructionsPayload);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const content = payload?.content ?? "";
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {payload?.filename
            ? t("society.card.instructions_file").replace("{0}", payload.filename)
            : t("society.card.description")}
        </h3>
        {!editing ? (
          <button
            type="button"
            onClick={() => {
              setDraft(content || payload?.template || "");
              setEditing(true);
            }}
            className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            <Pencil className="h-3 w-3" aria-hidden />
            {t("society.card.edit")}
          </button>
        ) : null}
      </div>
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={10}
            className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 font-mono text-[12px] leading-relaxed text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-strong"
          />
          {error ? (
            <p role="alert" className="text-[11px] text-destructive">
              {error}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={saving}>
              {t("society.create.cancel")}
            </Button>
            <Button size="sm" onClick={() => void save()} disabled={saving || !draft.trim()}>
              {saving ? t("society.card.saving") : t("society.card.save")}
            </Button>
          </div>
        </div>
      ) : content ? (
        <p className="whitespace-pre-line text-[13px] leading-relaxed text-foreground">{content}</p>
      ) : (
        <p className="text-xs text-muted-foreground">{t("society.card.instructions_empty")}</p>
      )}
      <p className="mt-1 text-[11px] text-muted-foreground">{t("society.card.instructions_hint")}</p>
    </div>
  );
}
