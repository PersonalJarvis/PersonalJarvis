/**
 * UltraWiki capability-slot settings — the four slots (storage, embedding,
 * distillation, rerank) rendered as provider CARDS, in the same visual and
 * behavioural language as the API-Keys view.
 *
 * Two rounds of "this is not the API-Keys section, it just looks vaguely like
 * it" got it here:
 *
 * 1. The slots used to be dropdowns whose credential hint pointed at the
 *    API-Keys view — which has no field for `voyage_api_key`,
 *    `mistral_api_key`, `cohere_api_key` or `ultrawiki_db_url`. Every provider
 *    now carries its credential widget on its own card, and that widget IS the
 *    shared `ApiKeyForm`, so the two screens cannot drift apart.
 * 2. The model was then a free-text box while every other provider surface in
 *    the app has a searchable picker. It is now the SAME picker
 *    (`BrainModelSelector`), fed by `GET /api/ultrawiki/models/{slot}` — live
 *    from the provider where one is listed, curated where none is, and its
 *    custom-id row still reaches a model no catalog knows yet. Picking saves;
 *    there is no separate Save button to forget.
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

import { BrainModelSelector } from "@/components/BrainModelSelector";
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
  SlotActiveControl,
  UltraProviderCard,
} from "@/components/ultrawiki/UltraProviderCard";
import {
  fetchUltraWikiCatalog,
  fetchUltraWikiSlotModels,
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

      <ReembedProgress status={status} />

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

/**
 * The shared model picker — literally the API-Keys one, pointed at this slot.
 *
 * `onSave` returns the picker's expected result shape; UltraWiki runs no
 * 1-token probe on a model pin (the slot's own Test button does a real call),
 * so `probe` is null and the picker simply shows the saved state.
 */
function SlotModelPicker({
  slot,
  providerId,
  currentModel,
  recommendedModel,
  onSave,
}: {
  slot: UltraWikiSlotName;
  providerId: string;
  currentModel: string;
  recommendedModel?: string | null;
  onSave: (model: string) => Promise<void>;
}): JSX.Element {
  return (
    <BrainModelSelector
      providerId={providerId}
      currentModel={currentModel}
      recommendedModel={recommendedModel ?? null}
      controlled
      loadModels={(refresh) =>
        fetchUltraWikiSlotModels(slot, providerId, refresh)
      }
      onSave={async (model) => {
        await onSave(model);
        return {
          ok: true,
          provider: providerId,
          model,
          persisted: true,
          applied_live: true,
          restart_required: false,
          probe: null,
        };
      }}
    />
  );
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
  const [endpoint, setEndpoint] = useState<string | null>(null);
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
                  <SlotModelPicker
                    slot="embedding"
                    providerId={row.id}
                    currentModel={catalog.models.embedding}
                    recommendedModel={row.default_model}
                    onSave={(model) =>
                      apply({
                        embedding_provider: row.id,
                        embedding_model: model,
                      })
                    }
                  />
                  {row.supports_base_url && (
                    <div className="space-y-2">
                      <SettingsField
                        label={t("ultrawiki.slots.server_url_label")}
                      >
                        <input
                          type="url"
                          value={endpointValue}
                          onChange={(e) => setEndpoint(e.target.value)}
                          placeholder={row.default_base_url ?? ""}
                          className={settingsInputCls}
                          data-testid="ultrawiki-ollama-endpoint-input"
                        />
                      </SettingsField>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={pending || endpointValue === catalog.ollama_endpoint}
                        onClick={() =>
                          void apply({ ollama_endpoint: endpointValue })
                        }
                        data-testid="ultrawiki-ollama-endpoint-apply"
                      >
                        {t("ultrawiki.slots.save_server_url")}
                      </Button>
                    </div>
                  )}
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
            slot="distill"
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
              onSelect={() =>
                void apply({
                  distill_provider: row.id,
                  // Models are provider-scoped. Carrying Gemini's model id
                  // into a Codex/Claude subscription card makes the first
                  // background call fail before the user can open the picker.
                  distill_model: row.default_model,
                })
              }
              onCredentialChanged={refresh}
            >
              {row.selected && (
                <SlotModelPicker
                  slot="distill"
                  providerId={row.id}
                  currentModel={catalog.models.distill}
                  recommendedModel={row.default_model}
                  onSave={(model) =>
                    apply({ distill_provider: row.id, distill_model: model })
                  }
                />
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
  const [floor, setFloor] = useState<string | null>(null);
  const rerankModel = status.slots.rerank?.model ?? "";
  const savedFloor = String(status.slots.rerank?.ranking?.rerank_min_score ?? 4);
  const floorValue = floor ?? savedFloor;

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
            slot="rerank"
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
                  rerank_model: row.id === "llm" ? rerankModel : "",
                })
              }
              onCredentialChanged={refresh}
            >
              {row.selected && row.id === "llm" && (
                <SlotModelPicker
                  slot="rerank"
                  providerId={row.id}
                  currentModel={rerankModel}
                  onSave={(model) =>
                    apply({ rerank_provider: row.id, rerank_model: model })
                  }
                />
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
            variant="secondary"
            disabled={pending || floorValue === savedFloor}
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
  // items-stretch (the grid default) plus h-full on the cards: a provider with
  // six lines of help text and one with three still produce a level row.
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
  slot,
  selected,
  busy,
  title,
  body,
  onSelect,
  testId,
}: {
  slot: UltraWikiSlotName;
  selected: boolean;
  busy: boolean;
  title: string;
  body: string;
  onSelect: () => void;
  testId: string;
}): JSX.Element {
  return (
    <div
      onClick={() => {
        if (!selected) onSelect();
      }}
      data-testid={testId}
      data-selected={selected ? "true" : "false"}
      className={cn(
        "card-outline flex h-full flex-col gap-3 p-4 transition-colors",
        selected
          ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30"
          : "cursor-pointer hover:border-primary/40 hover:bg-primary/[0.02]",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="font-display text-sm font-semibold tracking-tight">
          {title}
        </span>
        <SlotActiveControl
          slot={slot}
          selected={selected}
          busy={busy}
          onSelect={onSelect}
        />
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

/**
 * The background rebuild after an embedding-model switch.
 *
 * It is deliberately calm rather than a warning: search is NOT degraded while
 * this runs — the old vector space keeps answering until the new one is
 * complete. Without the line, though, the panel would look idle for hours
 * after a switch, which is how "did my change do anything?" starts.
 */
function ReembedProgress({ status }: { status: UltraWikiStatus }): JSX.Element | null {
  const t = useT();
  const reembed = status.reembed;
  if (!reembed?.model) return null;
  const total = Number(reembed.total ?? 0);
  const done = Math.min(Number(reembed.done ?? 0), total);
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div
      className="rounded-md border border-border bg-muted/40 px-4 py-3"
      data-testid="ultrawiki-reembed-progress"
    >
      <p className="text-sm text-muted-foreground">
        {t("ultrawiki.slots.reembed_progress")
          .replace("{0}", String(done))
          .replace("{1}", String(total))}
      </p>
      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-border"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${percent}%` }}
        />
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
