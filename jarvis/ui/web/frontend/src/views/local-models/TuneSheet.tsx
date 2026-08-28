/**
 * Tune sheet — the per-model options of one installed Ollama model.
 *
 * Props: `providerId` (the pull-capable card, "ollama" today), `model` (the
 * inventory row — its size, native context and capabilities shape the
 * chips) and an optional `onClose`. Mounted inline under the ledger row by
 * `InventoryPanel`; the roles panel may mount it the same way.
 *
 * The sheet edits a draft of `OllamaModelOptions` and writes the whole set
 * on Save (the backend has REPLACE semantics, so an unset knob is really
 * unset). Every knob carries one plain sentence; the ranges are the
 * backend's, which clamps rather than rejects. Knobs that bake into a
 * derived profile alias are named in the footnote, because the first use
 * after such a save takes a few seconds while Ollama builds that alias.
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, Sparkles, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { fill, useT } from "@/i18n";
import { IconButton, SoftButton } from "@/components/extensions/primitives";
import {
  compactOllamaModelOptions,
  needsProfileAlias,
  type OllamaModelOptions,
  type OllamaThinkLevel,
} from "@/lib/ollamaModelOptions";
import {
  useModelOptions,
  useResetModelOptions,
  useSaveModelOptions,
  useSuggestedOptions,
  type LocalModelRow,
} from "@/hooks/useLocalModels";
import { estimateContextGb, formatContext, toGb } from "./localModelsFormat";

export interface TuneSheetProps {
  providerId: string;
  model: LocalModelRow;
  onClose?: () => void;
}

/** Context sizes offered as chips; the native window is added when known. */
const CONTEXT_STEPS = [4096, 8192, 16384, 32768, 65536, 131072] as const;

type KeepAliveChip = { id: string; value: string | number | null };

const KEEP_ALIVE_CHIPS: KeepAliveChip[] = [
  { id: "default", value: null },
  { id: "5m", value: "5m" },
  { id: "30m", value: "30m" },
  { id: "2h", value: "2h" },
  { id: "forever", value: -1 },
  { id: "unload", value: 0 },
];

const THINK_CHIPS: { id: string; value: boolean | OllamaThinkLevel | null }[] =
  [
    { id: "default", value: null },
    { id: "off", value: false },
    { id: "low", value: "low" },
    { id: "medium", value: "medium" },
    { id: "high", value: "high" },
    { id: "max", value: "max" },
  ];

function Chip({
  active,
  onClick,
  children,
  hint,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  hint?: string;
  testId?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      data-testid={testId}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs tabular-nums transition-colors",
        active
          ? "border-primary/50 bg-primary/10 font-medium text-foreground"
          : "border-border text-muted-foreground hover:border-border hover:text-foreground",
      )}
    >
      {children}
      {hint ? (
        <span className="text-[10px] text-muted-foreground">{hint}</span>
      ) : null}
    </button>
  );
}

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
      {children}
    </div>
  );
}

function Knob({
  label,
  sentence,
  children,
}: {
  label: string;
  sentence: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="text-sm font-medium text-foreground">{label}</div>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
      <p className="text-xs text-muted-foreground">{sentence}</p>
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step,
  placeholder,
  testId,
}: {
  label: string;
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  testId?: string;
}) {
  return (
    <input
      type="number"
      aria-label={label}
      data-testid={testId}
      value={value ?? ""}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") return onChange(null);
        const n = Number(raw);
        onChange(Number.isFinite(n) ? n : null);
      }}
      className="h-7 w-28 rounded-md border border-border bg-background/60 px-2 text-xs tabular-nums placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
    />
  );
}

function sameKeepAlive(
  a: string | number | null | undefined,
  b: string | number | null,
): boolean {
  if (a === undefined || a === null) return b === null;
  return String(a) === String(b);
}

export function TuneSheet({ providerId, model, onClose }: TuneSheetProps) {
  const t = useT();
  const k = (key: string) => t(`local_models.tune.${key}`);

  const stored = useModelOptions(providerId, model.name);
  const suggested = useSuggestedOptions(providerId, model.name);
  const save = useSaveModelOptions(providerId);
  const reset = useResetModelOptions(providerId);

  const [draft, setDraft] = useState<OllamaModelOptions>({});
  const [samplingOpen, setSamplingOpen] = useState(false);
  const [readback, setReadback] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  // The draft follows the stored set once it arrives (and after a save/reset
  // invalidates it); edits in between are the user's and stay.
  const storedKey = stored.data ? JSON.stringify(stored.data.options) : null;
  useEffect(() => {
    if (stored.data) setDraft({ ...stored.data.options });
  }, [storedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const canThink = model.capabilities.includes("thinking");
  const modelGb = toGb(model.size_bytes);
  const native = model.context_length ?? null;

  const contextChips = useMemo(() => {
    const steps = CONTEXT_STEPS.filter((n) => native === null || n < native);
    const list: number[] = [...steps];
    if (native !== null && !list.includes(native)) list.push(native);
    return list;
  }, [native]);

  // The user may go above what fits this machine — with a warning, never a
  // refusal (maintainer rule 2026-08-24: their machine, their call).
  const suggestedCtx = suggested.data?.options.num_ctx ?? null;
  const overSuggested =
    suggestedCtx !== null &&
    suggestedCtx !== undefined &&
    typeof draft.num_ctx === "number" &&
    draft.num_ctx > suggestedCtx;

  const set = <K extends keyof OllamaModelOptions>(
    key: K,
    value: OllamaModelOptions[K],
  ) => {
    setReadback(null);
    setFailure(null);
    setDraft((d) => ({ ...d, [key]: value }));
  };

  const gpuMode: "default" | "all" | "cpu" | "custom" =
    draft.num_gpu === undefined || draft.num_gpu === null
      ? "default"
      : draft.num_gpu === -1
        ? "all"
        : draft.num_gpu === 0
          ? "cpu"
          : "custom";

  const compact = compactOllamaModelOptions(draft);
  const knobCount = Object.keys(compact).length;
  const alias = stored.data?.profile_alias ?? null;
  const willBake = needsProfileAlias(compact);

  const applySuggested = () => {
    if (!suggested.data) return;
    setReadback(null);
    setFailure(null);
    setDraft((d) => ({
      ...d,
      ...compactOllamaModelOptions(suggested.data.options),
    }));
  };

  const onSave = () => {
    setFailure(null);
    save.mutate(
      { name: model.name, options: compact },
      {
        onSuccess: (res) => {
          const count = Object.keys(res.options).length;
          setReadback(
            count === 0
              ? fill(k("saved_cleared"), { model: model.name })
              : res.profile_alias
                ? fill(k("saved_with_alias"), {
                    model: model.name,
                    count,
                    alias: res.profile_alias,
                  })
                : fill(k("saved"), { model: model.name, count }),
          );
        },
        onError: (err) =>
          setFailure(err instanceof Error ? err.message : String(err)),
      },
    );
  };

  const onReset = () => {
    setFailure(null);
    reset.mutate(model.name, {
      onSuccess: () => {
        setDraft({});
        setReadback(fill(k("reset_done"), { model: model.name }));
      },
      onError: (err) =>
        setFailure(err instanceof Error ? err.message : String(err)),
    });
  };

  const stopText = (draft.stop ?? []).join("\n");

  return (
    <section
      data-testid={`tune-sheet-${model.name}`}
      aria-label={fill(k("aria"), { model: model.name })}
      className="space-y-5 rounded-xl border border-border bg-card/60 p-4"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Eyebrow>{k("eyebrow")}</Eyebrow>
          <h3 className="mt-1 truncate font-display text-base font-semibold text-foreground">
            {model.name}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {fill(k("facts"), {
              size: modelGb ? `${modelGb.toFixed(1)} GB` : "—",
              context: formatContext(native),
            })}
          </p>
        </div>
        {onClose ? (
          <IconButton label={k("close")} onClick={onClose}>
            <X className="h-4 w-4" />
          </IconButton>
        ) : null}
      </div>

      {/* Suggested for this machine */}
      <div className="rounded-lg border border-border/70 bg-background/40 p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Sparkles className="h-4 w-4 text-primary" />
            {k("suggested_title")}
          </div>
          <SoftButton
            onClick={applySuggested}
            disabled={!suggested.data}
            ariaLabel={k("suggested_apply")}
          >
            {k("suggested_apply")}
          </SoftButton>
        </div>
        {suggested.isLoading && (
          <p className="mt-2 text-xs text-muted-foreground">
            {k("suggested_loading")}
          </p>
        )}
        {suggested.isError && (
          <p className="mt-2 text-xs text-foreground">
            {suggested.error instanceof Error
              ? suggested.error.message
              : k("suggested_failed")}
          </p>
        )}
        {suggested.data && (
          <ul
            className="mt-2 space-y-1 text-xs text-muted-foreground"
            data-testid="tune-reasons"
          >
            {suggested.data.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>

      {/* Context */}
      <div className="space-y-3">
        <Eyebrow>{k("group_context")}</Eyebrow>
        <Knob label={k("num_ctx")} sentence={k("num_ctx_sentence")}>
          <Chip
            active={draft.num_ctx === undefined || draft.num_ctx === null}
            onClick={() => set("num_ctx", null)}
          >
            {k("chip_default")}
          </Chip>
          {contextChips.map((n) => (
            <Chip
              key={n}
              active={draft.num_ctx === n}
              onClick={() => set("num_ctx", n)}
              hint={`~${estimateContextGb(n, modelGb).toFixed(1)} GB`}
              testId={`tune-ctx-${n}`}
            >
              {n === native
                ? fill(k("chip_native"), { context: formatContext(n) })
                : formatContext(n)}
            </Chip>
          ))}
        </Knob>
        {overSuggested && typeof draft.num_ctx === "number" && (
          <p
            className="text-xs text-foreground"
            data-testid="tune-ctx-warning"
          >
            {fill(k("num_ctx_over_suggested"), {
              context: formatContext(suggestedCtx as number),
              estimate: estimateContextGb(draft.num_ctx, modelGb).toFixed(1),
            })}
          </p>
        )}
      </div>

      {/* Placement */}
      <div className="space-y-3">
        <Eyebrow>{k("group_placement")}</Eyebrow>
        <Knob label={k("num_gpu")} sentence={k("num_gpu_sentence")}>
          <Chip
            active={gpuMode === "default"}
            onClick={() => set("num_gpu", null)}
          >
            {k("chip_default")}
          </Chip>
          <Chip
            active={gpuMode === "all"}
            onClick={() => set("num_gpu", -1)}
            testId="tune-gpu-all"
          >
            {k("gpu_all")}
          </Chip>
          <Chip
            active={gpuMode === "cpu"}
            onClick={() => set("num_gpu", 0)}
            testId="tune-gpu-cpu"
          >
            {k("gpu_cpu")}
          </Chip>
          <Chip active={gpuMode === "custom"} onClick={() => set("num_gpu", 1)}>
            {k("gpu_custom")}
          </Chip>
          {gpuMode === "custom" && (
            <NumberField
              label={k("gpu_layers")}
              value={draft.num_gpu}
              min={1}
              max={999}
              onChange={(v) => set("num_gpu", v ?? 1)}
              testId="tune-gpu-layers"
            />
          )}
        </Knob>
        <Knob label={k("num_thread")} sentence={k("num_thread_sentence")}>
          <NumberField
            label={k("num_thread")}
            value={draft.num_thread}
            min={0}
            max={512}
            placeholder={k("auto")}
            onChange={(v) => set("num_thread", v)}
            testId="tune-threads"
          />
        </Knob>
      </div>

      {/* Session */}
      <div className="space-y-3">
        <Eyebrow>{k("group_session")}</Eyebrow>
        <Knob label={k("keep_alive")} sentence={k("keep_alive_sentence")}>
          {KEEP_ALIVE_CHIPS.map((chip) => (
            <Chip
              key={chip.id}
              active={sameKeepAlive(draft.keep_alive, chip.value)}
              onClick={() => set("keep_alive", chip.value)}
              testId={`tune-keep-${chip.id}`}
            >
              {k(`keep_${chip.id}`)}
            </Chip>
          ))}
        </Knob>
        {canThink && (
          <Knob label={k("think")} sentence={k("think_sentence")}>
            {THINK_CHIPS.map((chip) => (
              <Chip
                key={chip.id}
                active={
                  chip.value === null
                    ? draft.think === undefined || draft.think === null
                    : draft.think === chip.value
                }
                onClick={() => set("think", chip.value)}
                testId={`tune-think-${chip.id}`}
              >
                {k(`think_${chip.id}`)}
              </Chip>
            ))}
          </Knob>
        )}
      </div>

      {/* Sampling, collapsed */}
      <div className="space-y-3">
        <button
          type="button"
          onClick={() => setSamplingOpen((v) => !v)}
          aria-expanded={samplingOpen}
          className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.18em] text-muted-foreground hover:text-foreground"
        >
          {k("group_sampling")}
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 transition-transform",
              samplingOpen && "rotate-180",
            )}
          />
        </button>
        {samplingOpen && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Knob label={k("temperature")} sentence={k("temperature_sentence")}>
              <NumberField
                label={k("temperature")}
                value={draft.temperature}
                min={0}
                max={2}
                step={0.1}
                onChange={(v) => set("temperature", v)}
                testId="tune-temperature"
              />
            </Knob>
            <Knob label={k("top_p")} sentence={k("top_p_sentence")}>
              <NumberField
                label={k("top_p")}
                value={draft.top_p}
                min={0}
                max={1}
                step={0.05}
                onChange={(v) => set("top_p", v)}
              />
            </Knob>
            <Knob label={k("top_k")} sentence={k("top_k_sentence")}>
              <NumberField
                label={k("top_k")}
                value={draft.top_k}
                min={0}
                max={1000}
                onChange={(v) => set("top_k", v)}
              />
            </Knob>
            <Knob label={k("min_p")} sentence={k("min_p_sentence")}>
              <NumberField
                label={k("min_p")}
                value={draft.min_p}
                min={0}
                max={1}
                step={0.05}
                onChange={(v) => set("min_p", v)}
              />
            </Knob>
            <Knob
              label={k("repeat_penalty")}
              sentence={k("repeat_penalty_sentence")}
            >
              <NumberField
                label={k("repeat_penalty")}
                value={draft.repeat_penalty}
                min={0}
                max={3}
                step={0.05}
                onChange={(v) => set("repeat_penalty", v)}
              />
            </Knob>
            <Knob label={k("seed")} sentence={k("seed_sentence")}>
              <NumberField
                label={k("seed")}
                value={draft.seed}
                min={0}
                max={2147483647}
                onChange={(v) => set("seed", v)}
              />
            </Knob>
            <Knob label={k("num_predict")} sentence={k("num_predict_sentence")}>
              <NumberField
                label={k("num_predict")}
                value={draft.num_predict}
                min={-2}
                max={1048576}
                onChange={(v) => set("num_predict", v)}
              />
            </Knob>
            <Knob label={k("stop")} sentence={k("stop_sentence")}>
              <textarea
                aria-label={k("stop")}
                value={stopText}
                rows={2}
                onChange={(e) => {
                  const lines = e.target.value
                    .split("\n")
                    .filter((l) => l.length > 0);
                  set("stop", lines.length ? lines : null);
                }}
                className="w-full rounded-md border border-border bg-background/60 px-2 py-1 text-xs placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </Knob>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="space-y-2 border-t border-border/70 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <SoftButton
            primary
            onClick={onSave}
            disabled={save.isPending}
            ariaLabel={k("save")}
          >
            {save.isPending ? k("saving") : k("save")}
          </SoftButton>
          <SoftButton
            onClick={onReset}
            disabled={reset.isPending || !stored.data?.configured}
            ariaLabel={k("reset")}
          >
            {k("reset")}
          </SoftButton>
          <span className="text-xs text-muted-foreground tabular-nums">
            {fill(k("knob_count"), { count: knobCount })}
          </span>
        </div>
        {readback && (
          <p
            className="text-sm text-muted-foreground"
            data-testid="tune-readback"
          >
            {readback}
          </p>
        )}
        {failure && (
          <p className="text-sm text-destructive" data-testid="tune-failure">
            {failure}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {alias
            ? fill(k("footnote_alias"), { alias })
            : willBake
              ? k("footnote_will_bake")
              : k("footnote_no_alias")}
        </p>
      </div>
    </section>
  );
}
