/**
 * UltraWiki capability-slot settings (design doc 04, decision D-2) — the
 * provider-slot cards modeled on the API-Keys section: current value, honest
 * ready/reason state, a real-call Test button per slot
 * (`POST /api/ultrawiki/test/{slot}`), and guarded changes via
 * `PUT /api/ultrawiki/settings`.
 *
 * The embedding change is the deliberate one (D-3): when vectors already
 * exist the backend answers 409 with the vector count, and this panel shows
 * the re-embed warning dialog before resending with `confirm_reembed=true`.
 * The Postgres connection string is a secret (`ultrawiki_db_url`) and is
 * never entered here — the card links to the API-Keys view instead.
 */
import { useState } from "react";
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
import {
  reembedGateOf,
  testUltraWikiSlot,
  updateUltraWikiSettings,
  type UltraWikiSettingsBody,
  type UltraWikiSlotTestResult,
  type UltraWikiStatus,
} from "@/lib/ultrawikiApi";

type SlotName = "embedding" | "distill" | "rerank" | "storage";

export function SlotsPanel({
  status,
  onChanged,
}: {
  status: UltraWikiStatus;
  onChanged: () => void;
}): JSX.Element {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [pending, setPending] = useState(false);
  const [reembedGate, setReembedGate] = useState<{
    body: UltraWikiSettingsBody;
    vectorItems: number;
  } | null>(null);

  // Local edits fall back to the server state until the user touches them
  // (the WikiProviderCard controlled-value idiom).
  const [dbBackend, setDbBackend] = useState<string | null>(null);
  const [embeddingProvider, setEmbeddingProvider] = useState<string | null>(
    null,
  );
  const [embeddingModel, setEmbeddingModel] = useState<string | null>(null);
  const [distillProvider, setDistillProvider] = useState<string | null>(null);
  const [rerankProvider, setRerankProvider] = useState<string | null>(null);

  const slots = status.slots;
  const dbValue = dbBackend ?? status.db_backend ?? "sqlite";
  const embeddingValue = embeddingProvider ?? slots.embedding?.provider ?? "";
  const embeddingModelValue = embeddingModel ?? slots.embedding?.model ?? "";
  const distillValue = distillProvider ?? slots.distill?.provider ?? "";
  const rerankValue = rerankProvider ?? slots.rerank?.provider ?? "";

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
      setDbBackend(null);
      setEmbeddingProvider(null);
      setEmbeddingModel(null);
      setDistillProvider(null);
      setRerankProvider(null);
      onChanged();
    } catch (e) {
      // The guarded embedding change: 409 carries the vector count; surface
      // the warning dialog instead of a bare error (D-3).
      const gate = reembedGateOf(e);
      if (gate) {
        setReembedGate({ body, vectorItems: gate.vector_items });
      } else {
        pushToast(
          "error",
          t("ultrawiki.slots.apply_failed").replace(
            "{0}",
            (e as Error).message,
          ),
        );
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-4 p-4" data-testid="ultrawiki-slots-panel">
      <h3 className="text-sm font-medium text-foreground">
        {t("ultrawiki.slots.title")}
      </h3>

      {/* Storage */}
      <SettingsBlock
        icon={Database}
        title={t("ultrawiki.slots.storage_title")}
        description={t("ultrawiki.slots.storage_desc")}
        headerRight={<SlotTestControl slot="storage" />}
      >
        <div className="space-y-3">
          <SettingsField label={t("ultrawiki.slots.storage_title")}>
            <select
              value={dbValue}
              onChange={(e) => setDbBackend(e.target.value)}
              className={settingsInputCls}
              data-testid="ultrawiki-storage-select"
            >
              <option value="sqlite">
                {t("ultrawiki.slots.storage_sqlite")}
              </option>
              <option value="postgres">
                {t("ultrawiki.slots.storage_postgres")}
              </option>
            </select>
          </SettingsField>
          {status.backend_in_use && (
            <p className="text-[11px] text-muted-foreground">
              {t("ultrawiki.slots.in_use").replace(
                "{0}",
                status.backend_in_use,
              )}
            </p>
          )}
          {dbValue === "postgres" && (
            <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
              {/* Connection strings are secrets (AP-2/AP-12): saved as
                  'ultrawiki_db_url' via the secrets surface, never typed here. */}
              <p>{t("ultrawiki.slots.storage_postgres_hint")}</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setActiveSection("apikeys")}
                data-testid="ultrawiki-open-apikeys"
              >
                {t("ultrawiki.slots.open_api_keys")}
              </Button>
            </div>
          )}
          <ApplyButton
            pending={pending}
            onClick={() => void apply({ db_backend: dbValue })}
            testId="ultrawiki-storage-apply"
          />
        </div>
      </SettingsBlock>

      {/* Embedding */}
      <SettingsBlock
        icon={Boxes}
        title={t("ultrawiki.slots.embedding_title")}
        description={t("ultrawiki.slots.embedding_desc")}
        headerRight={<SlotTestControl slot="embedding" />}
      >
        <div className="space-y-3">
          <SlotHealthLine
            ready={slots.embedding?.ready}
            reason={slots.embedding?.reason}
            current={
              slots.embedding?.provider
                ? `${slots.embedding.provider}${slots.embedding.model ? ` · ${slots.embedding.model}` : ""}`
                : ""
            }
          />
          <div className="grid gap-4 sm:grid-cols-2">
            <SettingsField label={t("ultrawiki.slots.provider_label")}>
              <select
                value={embeddingValue}
                onChange={(e) => {
                  setEmbeddingProvider(e.target.value);
                  setEmbeddingModel("");
                }}
                className={settingsInputCls}
                data-testid="ultrawiki-embedding-select"
              >
                {(slots.embedding?.available ?? []).map((option) => (
                  <option key={option.name} value={option.name}>
                    {option.name}
                    {!option.ready
                      ? ` (${t("ultrawiki.slots.not_ready_suffix")})`
                      : ""}
                  </option>
                ))}
              </select>
            </SettingsField>
            <SettingsField label={t("ultrawiki.slots.model_label")}>
              <input
                type="text"
                value={embeddingModelValue}
                onChange={(e) => setEmbeddingModel(e.target.value)}
                placeholder={t(
                  "ultrawiki.slots.model_default_placeholder",
                ).replace(
                  "{0}",
                  (slots.embedding?.available ?? []).find(
                    (o) => o.name === embeddingValue,
                  )?.default_model ?? "",
                )}
                className={settingsInputCls}
                data-testid="ultrawiki-embedding-model-input"
              />
            </SettingsField>
          </div>
          <ApplyButton
            pending={pending}
            onClick={() =>
              void apply({
                embedding_provider: embeddingValue,
                embedding_model: embeddingModelValue,
              })
            }
            testId="ultrawiki-embedding-apply"
          />
        </div>
      </SettingsBlock>

      {/* Distillation */}
      <SettingsBlock
        icon={FlaskConical}
        title={t("ultrawiki.slots.distill_title")}
        description={t("ultrawiki.slots.distill_desc")}
        headerRight={<SlotTestControl slot="distill" />}
      >
        <div className="space-y-3">
          <SlotHealthLine
            ready={slots.distill?.ready}
            reason={slots.distill?.reason}
            current={slots.distill?.provider ?? ""}
          />
          <SettingsField label={t("ultrawiki.slots.provider_label")}>
            <select
              value={distillValue}
              onChange={(e) => setDistillProvider(e.target.value)}
              className={settingsInputCls}
              data-testid="ultrawiki-distill-select"
            >
              <option value="">{t("ultrawiki.slots.distill_auto")}</option>
              {(slots.distill?.chain ?? [])
                .filter((name) => name !== "")
                .map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              {!(slots.distill?.chain ?? []).includes("ollama") && (
                <option value="ollama">ollama</option>
              )}
            </select>
          </SettingsField>
          <p className="text-[11px] text-muted-foreground">
            {(slots.distill?.chain ?? []).length > 0
              ? t("ultrawiki.slots.distill_chain").replace(
                  "{0}",
                  (slots.distill?.chain ?? []).join(", "),
                )
              : t("ultrawiki.slots.distill_chain_empty")}
          </p>
          <ApplyButton
            pending={pending}
            onClick={() => void apply({ distill_provider: distillValue })}
            testId="ultrawiki-distill-apply"
          />
        </div>
      </SettingsBlock>

      {/* Rerank */}
      <SettingsBlock
        icon={ListOrdered}
        title={t("ultrawiki.slots.rerank_title")}
        description={t("ultrawiki.slots.rerank_desc")}
        headerRight={<SlotTestControl slot="rerank" />}
      >
        <div className="space-y-3">
          <SlotHealthLine
            ready={slots.rerank?.ready}
            reason={slots.rerank?.reason}
            current={slots.rerank?.provider ?? ""}
          />
          <SettingsField label={t("ultrawiki.slots.provider_label")}>
            <select
              value={rerankValue}
              onChange={(e) => setRerankProvider(e.target.value)}
              className={settingsInputCls}
              data-testid="ultrawiki-rerank-select"
            >
              <option value="">{t("ultrawiki.slots.rerank_off")}</option>
              {(slots.rerank?.available ?? []).map((option) => (
                <option key={option.name} value={option.name}>
                  {option.name}
                  {!option.ready
                    ? ` (${t("ultrawiki.slots.not_ready_suffix")})`
                    : ""}
                </option>
              ))}
            </select>
          </SettingsField>
          <ApplyButton
            pending={pending}
            onClick={() => void apply({ rerank_provider: rerankValue })}
            testId="ultrawiki-rerank-apply"
          />
        </div>
      </SettingsBlock>

      {reembedGate && (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ultrawiki-reembed-title"
          data-testid="ultrawiki-reembed-dialog"
          onClick={(e) => {
            if (e.target === e.currentTarget) setReembedGate(null);
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
              {t("ultrawiki.slots.reembed_body").replace(
                "{0}",
                String(reembedGate.vectorItems),
              )}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setReembedGate(null)}
              >
                {t("ultrawiki.mode.cancel")}
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  const body = {
                    ...reembedGate.body,
                    confirm_reembed: true,
                  };
                  setReembedGate(null);
                  void apply(body);
                }}
                data-testid="ultrawiki-reembed-confirm"
              >
                {t("ultrawiki.slots.reembed_confirm").replace(
                  "{0}",
                  String(reembedGate.vectorItems),
                )}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ApplyButton({
  pending,
  onClick,
  testId,
}: {
  pending: boolean;
  onClick: () => void;
  testId: string;
}): JSX.Element {
  const t = useT();
  return (
    <Button size="sm" onClick={onClick} disabled={pending} data-testid={testId}>
      {pending && (
        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
      )}
      {t(pending ? "ultrawiki.slots.applying" : "ultrawiki.slots.apply")}
    </Button>
  );
}

/** Current value + honest ready/reason line (calm when ready, amber when not). */
function SlotHealthLine({
  ready,
  reason,
  current,
}: {
  ready: boolean | undefined;
  reason: string | undefined;
  current: string;
}): JSX.Element | null {
  const t = useT();
  if (!current && !reason) return null;
  return (
    <div className="space-y-0.5 text-[11px]">
      {current && (
        <p className="text-muted-foreground">
          {t("ultrawiki.slots.current").replace("{0}", current)}
        </p>
      )}
      {ready === false && reason && (
        <p className="text-[#ffb84d]">{reason}</p>
      )}
    </div>
  );
}

/** Per-slot real-call Test button + result chip (ApiKeysView test idiom). */
function SlotTestControl({ slot }: { slot: SlotName }): JSX.Element {
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
          {t(result.ok ? "ultrawiki.slots.test_ok" : "ultrawiki.slots.test_failed")}
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
