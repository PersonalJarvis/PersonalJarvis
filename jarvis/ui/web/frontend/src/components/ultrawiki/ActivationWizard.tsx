/**
 * One-time UltraWiki activation wizard (design doc 04, decisions D-3/D-6).
 *
 * Four steps — the one moment where the semi-permanent choices are made
 * consciously:
 *   1. Storage: SQLite (default, zero setup) or Postgres via the saved
 *      `ultrawiki_db_url` secret. The connection string is NEVER entered
 *      here — the card links to the API-Keys view where secrets live.
 *   2. Embedding: option cards from `GET /api/ultrawiki/providers` with each
 *      backend's honest ready/reason, plus the plain local-vs-cloud
 *      trade-off. A not-ready pick blocks Next — activation would 409 anyway.
 *   3. Distillation + rerank (both optional, both safe to change later). The
 *      distill default follows the embedding privacy stance (local embedding
 *      → local distillation) but is independently overridable.
 *   4. Review → `POST /api/ultrawiki/activate` → show the backend's
 *      `next_steps` (nothing is read before sources are approved).
 */
import { useState } from "react";
import {
  AlertTriangle,
  Check,
  Cloud,
  Database,
  HardDrive,
  Loader2,
  X,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { BrandedSelect } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  activateUltraWiki,
  fetchUltraWikiProviders,
  type UltraWikiProviderOption,
} from "@/lib/ultrawikiApi";

type StepId = "storage" | "embedding" | "optional" | "review";

const STEPS: readonly StepId[] = ["storage", "embedding", "optional", "review"];

const STEP_LABEL_KEY: Record<StepId, string> = {
  storage: "ultrawiki.wizard.step_storage",
  embedding: "ultrawiki.wizard.step_embedding",
  optional: "ultrawiki.wizard.step_optional",
  review: "ultrawiki.wizard.step_review",
};

export function ActivationWizard({
  onClose,
  onActivated,
}: {
  onClose: () => void;
  onActivated: () => void;
}): JSX.Element {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const [step, setStep] = useState<StepId>("storage");
  const [dbBackend, setDbBackend] = useState<"sqlite" | "postgres">("sqlite");
  const [embedding, setEmbedding] = useState<string>("");
  const [embeddingModel, setEmbeddingModel] = useState<string>("");
  // null = untouched → step 3 seeds the privacy-stance default on entry.
  const [distill, setDistill] = useState<string | null>(null);
  const [rerank, setRerank] = useState<string>("");
  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState<string>("");
  const [nextSteps, setNextSteps] = useState<string>("");

  const providersQuery = useQuery({
    queryKey: ["ultrawiki", "providers"],
    queryFn: fetchUltraWikiProviders,
    staleTime: 5_000,
  });
  const providers = providersQuery.data;

  const stepIndex = STEPS.indexOf(step);
  const selectedEmbedding = (providers?.embedding ?? []).find(
    (row) => row.name === embedding,
  );
  const postgresRow = (providers?.db_backends ?? []).find(
    (row) => row.name === "postgres",
  );
  const anyEmbeddingReady = (providers?.embedding ?? []).some(
    (row) => row.ready,
  );

  // The Next gate per step: storage blocks on a not-ready Postgres pick,
  // embedding blocks until a READY backend is chosen (the deliberate D-3 choice).
  const nextBlocked =
    (step === "storage" && dbBackend === "postgres" && !postgresRow?.ready) ||
    (step === "embedding" && (!selectedEmbedding || !selectedEmbedding.ready));

  function goNext() {
    if (nextBlocked) return;
    const next = STEPS[stepIndex + 1];
    if (!next) return;
    if (next === "optional" && distill === null) {
      // Suggest matching the embedding privacy stance (design doc 04 step 3):
      // a local (Ollama) embedding defaults to local distillation, cloud → auto.
      setDistill(embedding === "ollama" ? "ollama" : "");
    }
    setStep(next);
  }

  function goBack() {
    const prev = STEPS[stepIndex - 1];
    if (prev) setStep(prev);
  }

  async function handleActivate() {
    setActivating(true);
    setActivateError("");
    try {
      const result = await activateUltraWiki({
        db_backend: dbBackend,
        embedding_provider: embedding,
        embedding_model: embeddingModel.trim(),
        distill_provider: distill ?? "",
        rerank_provider: rerank,
      });
      setNextSteps(result.next_steps || "");
    } catch (e) {
      setActivateError((e as Error).message);
    } finally {
      setActivating(false);
    }
  }

  const activated = nextSteps !== "";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-background/80 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ultrawiki-wizard-title"
      data-testid="ultrawiki-activation-wizard"
      onClick={(e) => {
        if (e.target === e.currentTarget && !activating) onClose();
      }}
    >
      <div
        className="relative flex max-h-[85vh] w-full max-w-2xl flex-col overflow-y-auto rounded-2xl border border-border bg-card p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          onClick={onClose}
          className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground hover:bg-secondary/40 hover:text-foreground"
          aria-label={t("ultrawiki.wizard.close")}
          data-testid="ultrawiki-wizard-close"
        >
          <X className="h-4 w-4" />
        </button>

        <h2
          id="ultrawiki-wizard-title"
          className="text-base font-semibold text-foreground"
        >
          {t("ultrawiki.wizard.title")}
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {t("ultrawiki.wizard.subtitle")}
        </p>

        {/* Step rail */}
        <ol className="mt-4 flex flex-wrap items-center gap-2 text-[11px]">
          {STEPS.map((id, i) => (
            <li
              key={id}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-2.5 py-1",
                i === stepIndex
                  ? "border-primary/50 bg-primary/10 text-foreground"
                  : i < stepIndex
                    ? "border-border text-muted-foreground"
                    : "border-border/60 text-muted-foreground/60",
              )}
            >
              {i < stepIndex && <Check className="h-3 w-3" aria-hidden />}
              {t(STEP_LABEL_KEY[id])}
            </li>
          ))}
        </ol>

        <div className="mt-4 min-h-0 flex-1">
          {activated ? (
            <div className="space-y-3" data-testid="ultrawiki-wizard-done">
              <p className="text-sm leading-relaxed text-foreground">
                {nextSteps}
              </p>
              <Button size="sm" onClick={onActivated}>
                {t("ultrawiki.wizard.done")}
              </Button>
            </div>
          ) : providersQuery.isLoading ? (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("ultrawiki.panel.loading")}
            </div>
          ) : providersQuery.isError || !providers ? (
            <p className="py-6 text-sm text-destructive" role="alert">
              {t("ultrawiki.panel.status_unavailable")}
            </p>
          ) : (
            <>
              {step === "storage" && (
                <StorageStep
                  dbBackend={dbBackend}
                  onSelect={setDbBackend}
                  options={providers.db_backends}
                  onOpenApiKeys={() => setActiveSection("apikeys")}
                />
              )}
              {step === "embedding" && (
                <EmbeddingStep
                  options={providers.embedding}
                  selected={embedding}
                  onSelect={(name, defaultModel) => {
                    setEmbedding(name);
                    setEmbeddingModel(defaultModel);
                  }}
                  anyReady={anyEmbeddingReady}
                />
              )}
              {step === "optional" && (
                <OptionalStep
                  distill={distill ?? ""}
                  onDistill={setDistill}
                  rerank={rerank}
                  onRerank={setRerank}
                  rerankOptions={providers.rerank}
                />
              )}
              {step === "review" && (
                <ReviewStep
                  dbBackend={dbBackend}
                  embedding={embedding}
                  embeddingModel={
                    embeddingModel.trim() ||
                    selectedEmbedding?.default_model ||
                    ""
                  }
                  distill={distill ?? ""}
                  rerank={rerank}
                  error={activateError}
                />
              )}
            </>
          )}
        </div>

        {!activated && !providersQuery.isLoading && providers && (
          <div className="mt-5 flex items-center justify-between border-t border-border pt-4">
            <Button
              variant="outline"
              size="sm"
              onClick={goBack}
              disabled={stepIndex === 0 || activating}
            >
              {t("ultrawiki.wizard.back")}
            </Button>
            {step === "review" ? (
              <Button
                size="sm"
                onClick={() => void handleActivate()}
                disabled={activating}
                data-testid="ultrawiki-wizard-activate"
              >
                {activating && (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                )}
                {t(
                  activating
                    ? "ultrawiki.wizard.activating"
                    : "ultrawiki.wizard.activate",
                )}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={goNext}
                disabled={nextBlocked}
                data-testid="ultrawiki-wizard-next"
              >
                {t("ultrawiki.wizard.next")}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StorageStep({
  dbBackend,
  onSelect,
  options,
  onOpenApiKeys,
}: {
  dbBackend: "sqlite" | "postgres";
  onSelect: (backend: "sqlite" | "postgres") => void;
  options: Array<{ name: string; ready: boolean; reason: string; detail: string }>;
  onOpenApiKeys: () => void;
}): JSX.Element {
  const t = useT();
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {t("ultrawiki.wizard.storage_intro")}
      </p>
      <ul className="space-y-2">
        {options.map((option) => {
          const isPostgres = option.name === "postgres";
          const selected = dbBackend === option.name;
          return (
            <li key={option.name}>
              <button
                type="button"
                onClick={() => onSelect(option.name as "sqlite" | "postgres")}
                aria-pressed={selected}
                data-testid={`ultrawiki-wizard-storage-${option.name}`}
                className={cn(
                  "w-full rounded-xl border p-3 text-left transition-colors",
                  selected
                    ? "border-primary/60 bg-primary/5 ring-1 ring-primary/40"
                    : "border-border hover:bg-secondary/30",
                )}
              >
                <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                  {isPostgres ? (
                    <Database className="h-4 w-4 text-primary" aria-hidden />
                  ) : (
                    <HardDrive className="h-4 w-4 text-primary" aria-hidden />
                  )}
                  {t(
                    isPostgres
                      ? "ultrawiki.slots.storage_postgres"
                      : "ultrawiki.slots.storage_sqlite",
                  )}
                </span>
                <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                  {option.detail}
                </span>
                {!option.ready && (
                  <span
                    className="mt-1.5 flex items-start gap-1.5 text-xs text-[#ffb84d]"
                    data-testid="ultrawiki-wizard-storage-not-ready"
                  >
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    {option.reason}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {dbBackend === "postgres" && (
        <div className="space-y-2 rounded-lg border border-border/60 bg-muted/20 p-3 text-xs text-muted-foreground">
          {/* The connection string is a secret: it lives in the secret chain,
              never in this wizard (AP-2/AP-12). */}
          <p>{t("ultrawiki.slots.storage_postgres_hint")}</p>
          <Button variant="outline" size="sm" onClick={onOpenApiKeys}>
            {t("ultrawiki.slots.open_api_keys")}
          </Button>
        </div>
      )}
    </div>
  );
}

function EmbeddingStep({
  options,
  selected,
  onSelect,
  anyReady,
}: {
  options: UltraWikiProviderOption[];
  selected: string;
  onSelect: (name: string, defaultModel: string) => void;
  anyReady: boolean;
}): JSX.Element {
  const t = useT();
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {t("ultrawiki.wizard.embedding_intro")}
      </p>
      {/* The honest local-vs-cloud trade-off, stated plainly (design doc 04). */}
      <p className="rounded-lg border border-border/60 bg-muted/20 p-3 text-xs leading-relaxed text-muted-foreground">
        {t("ultrawiki.wizard.embedding_tradeoff")}
      </p>
      <p className="flex items-start gap-1.5 text-xs text-[#ffb84d]">
        <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
        {t("ultrawiki.wizard.embedding_permanent")}
      </p>
      {!anyReady && (
        <p
          className="text-xs text-destructive"
          role="alert"
          data-testid="ultrawiki-wizard-none-ready"
        >
          {t("ultrawiki.wizard.embedding_none_ready")}
        </p>
      )}
      <ul className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => {
          const isLocal = option.name === "ollama";
          const isSelected = selected === option.name;
          return (
            <li key={option.name}>
              <button
                type="button"
                onClick={() => onSelect(option.name, option.default_model ?? "")}
                aria-pressed={isSelected}
                data-testid={`ultrawiki-wizard-embedding-${option.name}`}
                className={cn(
                  "h-full w-full rounded-xl border p-3 text-left transition-colors",
                  isSelected
                    ? "border-primary/60 bg-primary/5 ring-1 ring-primary/40"
                    : "border-border hover:bg-secondary/30",
                  !option.ready && "opacity-80",
                )}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium text-foreground">
                    {option.name}
                  </span>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px]",
                      isLocal
                        ? "border-[#5bd4a4]/40 text-[#5bd4a4]"
                        : "border-border text-muted-foreground",
                    )}
                  >
                    {isLocal ? (
                      <HardDrive className="h-2.5 w-2.5" aria-hidden />
                    ) : (
                      <Cloud className="h-2.5 w-2.5" aria-hidden />
                    )}
                    {t(
                      isLocal
                        ? "ultrawiki.wizard.local_chip"
                        : "ultrawiki.wizard.cloud_chip",
                    )}
                  </span>
                </span>
                {option.default_model && (
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {t("ultrawiki.wizard.default_model").replace(
                      "{0}",
                      option.default_model,
                    )}
                  </span>
                )}
                {!option.ready && (
                  <span
                    className="mt-1.5 block text-xs text-[#ffb84d]"
                    data-testid={`ultrawiki-wizard-embedding-${option.name}-reason`}
                  >
                    {t("ultrawiki.wizard.embedding_not_ready").replace(
                      "{0}",
                      option.reason,
                    )}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function OptionalStep({
  distill,
  onDistill,
  rerank,
  onRerank,
  rerankOptions,
}: {
  distill: string;
  onDistill: (value: string) => void;
  rerank: string;
  onRerank: (value: string) => void;
  rerankOptions: UltraWikiProviderOption[];
}): JSX.Element {
  const t = useT();
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        {t("ultrawiki.wizard.optional_intro")}
      </p>
      <label className="block">
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.wizard.distill_label")}
        </span>
        <BrandedSelect
          value={distill}
          onValueChange={onDistill}
          ariaLabel={t("ultrawiki.wizard.distill_label")}
          testId="ultrawiki-wizard-distill"
          options={[
            { value: "", label: t("ultrawiki.wizard.distill_auto") },
            {
              value: "ollama",
              label: t("ultrawiki.wizard.distill_local"),
            },
          ]}
        />
        <span className="mt-1 block text-[11px] text-muted-foreground">
          {t("ultrawiki.wizard.distill_privacy_hint")}
        </span>
      </label>
      <label className="block">
        <span className="mb-1.5 block text-[10px] uppercase tracking-wider text-muted-foreground">
          {t("ultrawiki.wizard.rerank_label")}
        </span>
        <BrandedSelect
          value={rerank}
          onValueChange={onRerank}
          ariaLabel={t("ultrawiki.wizard.rerank_label")}
          testId="ultrawiki-wizard-rerank"
          options={[
            { value: "", label: t("ultrawiki.wizard.rerank_off") },
            ...rerankOptions.map((option) => ({
              value: option.name,
              label: `${option.name}${
                option.ready
                  ? ""
                  : ` (${t("ultrawiki.slots.not_ready_suffix")})`
              }`,
              disabled: !option.ready,
            })),
          ]}
        />
      </label>
    </div>
  );
}

function ReviewStep({
  dbBackend,
  embedding,
  embeddingModel,
  distill,
  rerank,
  error,
}: {
  dbBackend: string;
  embedding: string;
  embeddingModel: string;
  distill: string;
  rerank: string;
  error: string;
}): JSX.Element {
  const t = useT();
  const rows: Array<[string, string]> = [
    [t("ultrawiki.wizard.review_storage"), dbBackend],
    [
      t("ultrawiki.wizard.review_embedding"),
      embeddingModel ? `${embedding} · ${embeddingModel}` : embedding,
    ],
    [
      t("ultrawiki.wizard.review_distill"),
      distill || t("ultrawiki.wizard.auto"),
    ],
    [t("ultrawiki.wizard.review_rerank"), rerank || t("ultrawiki.wizard.off")],
  ];
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {t("ultrawiki.wizard.review_intro")}
      </p>
      <dl className="space-y-1.5 rounded-lg border border-border/60 bg-muted/20 p-3 text-sm">
        {rows.map(([label, value]) => (
          <div className="flex items-baseline justify-between gap-3" key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="font-mono text-xs text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      {error && (
        <p
          className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
          role="alert"
          data-testid="ultrawiki-wizard-error"
        >
          {t("ultrawiki.wizard.activate_failed").replace("{0}", error)}
        </p>
      )}
    </div>
  );
}
