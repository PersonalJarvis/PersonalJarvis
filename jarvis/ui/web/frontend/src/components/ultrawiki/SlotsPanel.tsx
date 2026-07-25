/**
 * UltraWiki capability-slot settings — the four slots (storage, embedding,
 * distillation, rerank) rendered as provider CARDS, in the same visual and
 * behavioural language as the API-Keys view.
 *
 * What changed and why (2026-07-25): the previous version showed a dropdown
 * per slot and, for anything needing a credential, the sentence "add it in the
 * API-Keys view". That view has no field for these slots, so Voyage, Mistral,
 * Cohere and every Postgres store were listed but impossible to connect from
 * inside the app — the exact out-of-app setup step the in-app-recoverable
 * mandate forbids (§3, AP-23). Every provider now carries its credential
 * widget on its own card, using the SAME `ApiKeyForm` component the API-Keys
 * view uses, so the two screens cannot drift apart.
 *
 * Slot rules that are deliberate, not incidental:
 *
 * - **Embedding has no automatic option and no cross-provider fallback (D-3).**
 *   The (provider, model) pair pins the vector space of the whole corpus, so
 *   changing it re-embeds everything — the backend answers 409 with the vector
 *   count and this panel shows the warning dialog before confirming.
 * - **Distillation and rerank DO cross provider families (AP-22)** and default
 *   to "automatic" / "off": leaving them unset is a working configuration, not
 *   an unfinished one.
 * - **Storage always has a working floor.** SQLite needs nothing; a cloud
 *   preset that is not connected yet degrades to it with an honest line rather
 *   than breaking the wiki.
 * - **The relevance floor lives on the rerank slot**, with the stage that
 *   produces the grade it gates on. It governs only what UltraWiki volunteers
 *   unasked; an explicit search always shows everything.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Boxes,
  Database,
  FlaskConical,
  ListOrdered,
  Loader2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  SettingsBlock,
  SettingsField,
  settingsInputCls,
} from "@/views/settings/SettingsBlock";
import { SupabaseConnect } from "@/components/ultrawiki/SupabaseConnect";
import {
  StateChip,
  UltraProviderCard,
} from "@/components/ultrawiki/UltraProviderCard";
import {
  fetchUltraWikiCatalog,
  reembedGateOf,
  testUltraWikiSlot,
  updateUltraWikiSettings,
  type UltraWikiCatalog,
  type UltraWikiSettingsBody,
  type UltraWikiSlotName,
  type UltraWikiSlotTestResult,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

export function SlotsPanel({
  status,
  onChanged,
}: {
  status: UltraWikiStatus;
  onChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);

  const catalogQuery = useQuery({
    queryKey: ["ultrawiki", "catalog"],
    queryFn: fetchUltraWikiCatalog,
    staleTime: 5_000,
  });

  const [pending, setPending] = useState(false);
  const [reembedGate, setReembedGate] = useState<{
    body: UltraWikiSettingsBody;
    vectorItems: number;
  } | null>(null);

  const catalog = catalogQuery.data ?? null;
  const refresh = () => {
    void catalogQuery.refetch();
    onChanged();
  };

  async function apply(body: UltraWikiSettingsBody) {
    setPending(true);
    try {
      const result = await updateUltraWikiSettings(body);
      pushToast(
        "success",
        result.reembed_started
          ? t("ultrawiki.slots.reembed_started")
          : t("ultrawiki.slots.applied"),
      );
      refresh();
    } catch (e) {
      // The guarded embedding change: 409 carries the vector count, so the
      // user is warned about the re-embed BEFORE it starts (D-3).
      const gate = reembedGateOf(e);
      if (gate) {
        setReembedGate({ body, vectorItems: gate.vector_items });
      } else {
        pushToast(
          "error",
          t("ultrawiki.slots.apply_failed").replace("{0}", (e as Error).message),
        );
      }
    } finally {
      setPending(false);
    }
  }

  if (catalogQuery.isLoading) {
    return (
      <div
        className="p-6 text-sm text-muted-foreground"
        data-testid="ultrawiki-slots-loading"
      >
        {t("ultrawiki.slots.loading")}
      </div>
    );
  }

  if (!catalog) {
    return (
      <div className="p-4">
        <div
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          data-testid="ultrawiki-slots-unavailable"
        >
          {t("ultrawiki.slots.catalog_unavailable")}
        </div>
      </div>
    );
  }

  const shared = { catalog, status, pending, apply, refresh };

  return (
    <div className="space-y-4 p-4" data-testid="ultrawiki-slots-panel">
      <h3 className="text-sm font-medium text-foreground">
        {t("ultrawiki.slots.title")}
      </h3>

      <StorageSection {...shared} />
      <EmbeddingSection {...shared} />
      <DistillSection {...shared} />
      <RerankSection {...shared} />

      {reembedGate && (
        <ReembedDialog
          vectorItems={reembedGate.vectorItems}
          onCancel={() => setReembedGate(null)}
          onConfirm={() => {
            const body = { ...reembedGate.body, confirm_reembed: true };
            setReembedGate(null);
            void apply(body);
          }}
        />
      )}
    </div>
  );
}

interface SectionProps {
  catalog: UltraWikiCatalog;
  status: UltraWikiStatus;
  pending: boolean;
  apply: (body: UltraWikiSettingsBody) => Promise<void>;
  refresh: () => void;
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

function StorageSection({
  catalog,
  status,
  pending,
  apply,
  refresh,
}: SectionProps): JSX.Element {
  const t = useT();
  const rows = catalog.slots.storage ?? [];
  return (
    <SettingsBlock
      icon={Database}
      title={t("ultrawiki.slots.storage_title")}
      description={t("ultrawiki.slots.storage_desc")}
      headerRight={<SlotTestControl slot="storage" />}
    >
      <div className="space-y-3">
        {status.backend_in_use && (
          <p className="text-[11px] text-muted-foreground">
            {t("ultrawiki.slots.in_use").replace("{0}", status.backend_in_use)}
          </p>
        )}
        <CardGrid>
          {rows.map((row) => (
            <UltraProviderCard
              key={row.id}
              row={row}
              busy={pending}
              onSelect={() => void apply({ storage_provider: row.id })}
              onCredentialChanged={refresh}
              footer={
                row.connection_hint ? (
                  <p className="break-all font-mono text-[10px] text-muted-foreground">
                    {row.connection_hint}
                  </p>
                ) : undefined
              }
            >
              {row.id === "supabase" && (
                <SupabaseConnect row={row} onChanged={refresh} />
              )}
            </UltraProviderCard>
          ))}
        </CardGrid>
        {/* A store change only takes effect when the store is reopened; saying
            so beats a user wondering why their pages are still in SQLite. */}
        <p className="text-[11px] text-muted-foreground">
          {t("ultrawiki.slots.storage_restart_hint")}
        </p>
      </div>
    </SettingsBlock>
  );
}

// ---------------------------------------------------------------------------
// Embedding
// ---------------------------------------------------------------------------

function EmbeddingSection({
  catalog,
  status,
  pending,
  apply,
  refresh,
}: SectionProps): JSX.Element {
  const t = useT();
  const rows = catalog.slots.embedding ?? [];
  const [model, setModel] = useState<string | null>(null);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const modelValue = model ?? catalog.models.embedding;
  const endpointValue = endpoint ?? catalog.ollama_endpoint;

  return (
    <SettingsBlock
      icon={Boxes}
      title={t("ultrawiki.slots.embedding_title")}
      description={t("ultrawiki.slots.embedding_desc")}
      headerRight={<SlotTestControl slot="embedding" />}
    >
      <div className="space-y-3">
        <SlotProvenance slot="embedding" via={status.slots.embedding?.via} />
        <CardGrid>
          {rows.map((row) => (
            <UltraProviderCard
              key={row.id}
              row={row}
              busy={pending}
              // The model travels WITH the provider switch: applying them
              // separately would trigger the expensive re-embed twice.
              onSelect={() =>
                void apply({
                  embedding_provider: row.id,
                  embedding_model: row.default_model,
                })
              }
              onCredentialChanged={refresh}
            >
              {row.selected && (
                <div className="space-y-3">
                  <SettingsField label={t("ultrawiki.slots.model_label")}>
                    <input
                      type="text"
                      value={modelValue}
                      onChange={(e) => setModel(e.target.value)}
                      placeholder={row.default_model}
                      className={settingsInputCls}
                      data-testid="ultrawiki-embedding-model-input"
                    />
                  </SettingsField>
                  {row.supports_base_url && (
                    <SettingsField label={t("ultrawiki.slots.server_url_label")}>
                      <input
                        type="url"
                        value={endpointValue}
                        onChange={(e) => setEndpoint(e.target.value)}
                        placeholder={row.default_base_url ?? ""}
                        className={settingsInputCls}
                        data-testid="ultrawiki-ollama-endpoint-input"
                      />
                    </SettingsField>
                  )}
                  <Button
                    size="sm"
                    disabled={pending}
                    onClick={() =>
                      void apply({
                        embedding_provider: row.id,
                        embedding_model: modelValue,
                        ...(row.supports_base_url
                          ? { ollama_endpoint: endpointValue }
                          : {}),
                      })
                    }
                    data-testid="ultrawiki-embedding-apply"
                  >
                    {pending && (
                      <Loader2
                        className="mr-1 h-3.5 w-3.5 animate-spin"
                        aria-hidden
                      />
                    )}
                    {t("ultrawiki.slots.save_model")}
                  </Button>
                </div>
              )}
            </UltraProviderCard>
          ))}
        </CardGrid>
        <p className="text-[11px] text-muted-foreground">
          {t("ultrawiki.slots.embedding_lock_hint")}
        </p>
      </div>
    </SettingsBlock>
  );
}

// ---------------------------------------------------------------------------
// Distillation
// ---------------------------------------------------------------------------

function DistillSection({
  catalog,
  status,
  pending,
  apply,
  refresh,
}: SectionProps): JSX.Element {
  const t = useT();
  const rows = catalog.slots.distill ?? [];
  const [model, setModel] = useState<string | null>(null);
  const modelValue = model ?? catalog.models.distill;
  const automatic = !catalog.selected.distill;

  return (
    <SettingsBlock
      icon={FlaskConical}
      title={t("ultrawiki.slots.distill_title")}
      description={t("ultrawiki.slots.distill_desc")}
      headerRight={<SlotTestControl slot="distill" />}
    >
      <div className="space-y-3">
        <SlotProvenance slot="distill" via={status.slots.distill?.via} />
        <CardGrid>
          <SlotDefaultCard
            selected={automatic}
            busy={pending}
            title={t("ultrawiki.slots.distill_auto")}
            body={t("ultrawiki.slots.distill_auto_desc")}
            onSelect={() => void apply({ distill_provider: "" })}
            testId="ultrawiki-card-distill-auto"
          />
          {rows.map((row) => (
            <UltraProviderCard
              key={row.id}
              row={row}
              busy={pending}
              onSelect={() => void apply({ distill_provider: row.id })}
              onCredentialChanged={refresh}
            >
              {row.selected && (
                <div className="space-y-2">
                  <SettingsField label={t("ultrawiki.slots.model_label")}>
                    <input
                      type="text"
                      value={modelValue}
                      onChange={(e) => setModel(e.target.value)}
                      placeholder={
                        row.default_model ||
                        t("ultrawiki.slots.model_cheap_default")
                      }
                      className={settingsInputCls}
                      data-testid="ultrawiki-distill-model-input"
                    />
                  </SettingsField>
                  <Button
                    size="sm"
                    disabled={pending}
                    onClick={() =>
                      void apply({
                        distill_provider: row.id,
                        distill_model: modelValue,
                      })
                    }
                    data-testid="ultrawiki-distill-apply"
                  >
                    {t("ultrawiki.slots.save_model")}
                  </Button>
                </div>
              )}
            </UltraProviderCard>
          ))}
        </CardGrid>
      </div>
    </SettingsBlock>
  );
}

// ---------------------------------------------------------------------------
// Rerank
// ---------------------------------------------------------------------------

function RerankSection({
  catalog,
  status,
  pending,
  apply,
  refresh,
}: SectionProps): JSX.Element {
  const t = useT();
  const rows = catalog.slots.rerank ?? [];
  const off = !catalog.selected.rerank;
  const [rerankModel, setRerankModel] = useState<string | null>(null);
  const [floor, setFloor] = useState<string | null>(null);
  const rerankModelValue = rerankModel ?? status.slots.rerank?.model ?? "";
  const floorValue =
    floor ?? String(status.slots.rerank?.ranking?.rerank_min_score ?? 4);

  return (
    <SettingsBlock
      icon={ListOrdered}
      title={t("ultrawiki.slots.rerank_title")}
      description={t("ultrawiki.slots.rerank_desc")}
      headerRight={<SlotTestControl slot="rerank" />}
    >
      <div className="space-y-3">
        <SlotProvenance slot="rerank" via={status.slots.rerank?.via} />
        <CardGrid>
          <SlotDefaultCard
            selected={off}
            busy={pending}
            title={t("ultrawiki.slots.rerank_off")}
            body={t("ultrawiki.slots.rerank_off_desc")}
            onSelect={() =>
              void apply({ rerank_provider: "", rerank_model: "" })
            }
            testId="ultrawiki-card-rerank-off"
          />
          {rows.map((row) => (
            <UltraProviderCard
              key={row.id}
              row={row}
              busy={pending}
              onSelect={() =>
                void apply({
                  rerank_provider: row.id,
                  // The vendor cross-encoders pin their own model; only the
                  // chat-graded backend takes one from the user.
                  rerank_model: row.id === "llm" ? rerankModelValue : "",
                })
              }
              onCredentialChanged={refresh}
            >
              {row.selected && row.id === "llm" && (
                <div className="space-y-2">
                  <SettingsField label={t("ultrawiki.slots.model_label")}>
                    <input
                      type="text"
                      value={rerankModelValue}
                      onChange={(e) => setRerankModel(e.target.value)}
                      placeholder={t("ultrawiki.slots.rerank_model_placeholder")}
                      className={settingsInputCls}
                      data-testid="ultrawiki-rerank-model-input"
                    />
                  </SettingsField>
                  <Button
                    size="sm"
                    disabled={pending}
                    onClick={() =>
                      void apply({
                        rerank_provider: row.id,
                        rerank_model: rerankModelValue,
                      })
                    }
                    data-testid="ultrawiki-rerank-apply"
                  >
                    {t("ultrawiki.slots.save_model")}
                  </Button>
                </div>
              )}
            </UltraProviderCard>
          ))}
        </CardGrid>

        {/* The relevance floor lives with the stage that produces the grade it
            gates on. Explicit searches always show everything; this only
            governs what UltraWiki volunteers on its own. */}
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3">
          <SettingsField label={t("ultrawiki.slots.floor_label")}>
            <input
              type="number"
              min={0}
              max={10}
              step={0.5}
              value={floorValue}
              onChange={(e) => setFloor(e.target.value)}
              className={settingsInputCls}
              data-testid="ultrawiki-rerank-floor-input"
            />
          </SettingsField>
          <p className="text-[11px] text-muted-foreground">
            {t("ultrawiki.slots.floor_hint")}
          </p>
          <Button
            size="sm"
            disabled={pending}
            onClick={() => void apply({ rerank_min_score: Number(floorValue) })}
            data-testid="ultrawiki-rerank-floor-apply"
          >
            {t("ultrawiki.slots.apply")}
          </Button>
        </div>
      </div>
    </SettingsBlock>
  );
}

// ---------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------

function CardGrid({ children }: { children: React.ReactNode }): JSX.Element {
  return <div className="grid gap-3 lg:grid-cols-2">{children}</div>;
}

/**
 * Key-free provenance: WHICH credential or endpoint actually makes this slot
 * work right now. With cross-family fallback chains, the provider a user
 * picked and the one answering can differ — saying so turns "why is this
 * still working / not working" into a one-line answer.
 */
function SlotProvenance({
  slot,
  via,
}: {
  slot: UltraWikiSlotName;
  via: string | undefined;
}): JSX.Element | null {
  const t = useT();
  if (!via) return null;
  return (
    <p
      className="text-[11px] text-muted-foreground"
      data-testid={`ultrawiki-slot-via-${slot}`}
    >
      {t("ultrawiki.slots.via").replace("{0}", via)}
    </p>
  );
}

/**
 * The "no provider" card — Automatic for distillation, Off for rerank.
 *
 * A first-class card rather than an empty dropdown entry, because for both
 * slots it is a legitimate and often correct choice: distillation without a
 * pin uses whatever credential the user actually has, and search works fine
 * with the plain fusion order.
 */
function SlotDefaultCard({
  selected,
  busy,
  title,
  body,
  onSelect,
  testId,
}: {
  selected: boolean;
  busy: boolean;
  title: string;
  body: string;
  onSelect: () => void;
  testId: string;
}): JSX.Element {
  const t = useT();
  return (
    <div
      onClick={() => {
        if (!selected) onSelect();
      }}
      data-testid={testId}
      data-selected={selected ? "true" : "false"}
      className={cn(
        "card-outline space-y-3 p-4 transition-colors",
        selected
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-display text-sm font-semibold tracking-tight">
              {title}
            </span>
            {selected && (
              <StateChip tone="active">
                {t("ultrawiki.card.chip_in_use")}
              </StateChip>
            )}
          </div>
        </div>
        <Button
          size="sm"
          variant={selected ? "secondary" : "outline"}
          disabled={selected || busy}
          onClick={onSelect}
        >
          {t(selected ? "ultrawiki.card.in_use" : "ultrawiki.card.use")}
        </Button>
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">{body}</p>
    </div>
  );
}

function ReembedDialog({
  vectorItems,
  onCancel,
  onConfirm,
}: {
  vectorItems: number;
  onCancel: () => void;
  onConfirm: () => void;
}): JSX.Element {
  const t = useT();
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ultrawiki-reembed-title"
      data-testid="ultrawiki-reembed-dialog"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id="ultrawiki-reembed-title"
          className="text-base font-semibold text-foreground"
        >
          {t("ultrawiki.slots.reembed_title")}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {t("ultrawiki.slots.reembed_body").replace("{0}", String(vectorItems))}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onCancel}>
            {t("ultrawiki.mode.cancel")}
          </Button>
          <Button
            size="sm"
            onClick={onConfirm}
            data-testid="ultrawiki-reembed-confirm"
          >
            {t("ultrawiki.slots.reembed_confirm").replace(
              "{0}",
              String(vectorItems),
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Per-slot real-call Test button + result chip (the API-Keys test idiom). */
function SlotTestControl({ slot }: { slot: UltraWikiSlotName }): JSX.Element {
  const t = useT();
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<UltraWikiSlotTestResult | null>(null);

  async function handleTest() {
    setTesting(true);
    setResult(null);
    try {
      setResult(await testUltraWikiSlot(slot));
    } catch (e) {
      setResult({ ok: false, detail: (e as Error).message, latency_ms: 0 });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {result && (
        <span
          className={cn(
            "max-w-[16rem] truncate rounded-full border px-2 py-0.5 text-[10px]",
            result.ok
              ? "border-[#5bd4a4]/40 bg-[#5bd4a4]/10 text-[#5bd4a4]"
              : "border-destructive/40 bg-destructive/10 text-destructive",
          )}
          title={result.detail}
          data-testid={`ultrawiki-test-result-${slot}`}
          data-ok={result.ok ? "true" : "false"}
        >
          {t(
            result.ok ? "ultrawiki.slots.test_ok" : "ultrawiki.slots.test_failed",
          )}
          {" · "}
          {result.detail}
        </span>
      )}
      <Button
        variant="outline"
        size="sm"
        onClick={() => void handleTest()}
        disabled={testing}
        data-testid={`ultrawiki-test-${slot}`}
      >
        {testing && (
          <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
        )}
        {t(testing ? "ultrawiki.slots.testing" : "ultrawiki.slots.test")}
      </Button>
    </div>
  );
}
