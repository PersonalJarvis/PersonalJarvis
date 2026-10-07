import { ArrowRight, BookOpen, Cloud, Cpu } from "lucide-react";

import type { DictationStatus } from "@/hooks/useDictation";
import { useT } from "@/i18n";

/**
 * Which recognizer answers the next press (P-41) — the settings name one, the
 * lane may hold another, and this card says which. It used to be a second grey
 * sentence under the "Ready" line; as its own small card it can carry a plain
 * "On this computer" / "Cloud" headline, with the exact model and fallback
 * under it for whoever wants them.
 */
export function DictationEngineCard({
  engine,
}: {
  engine: NonNullable<DictationStatus["engine"]>;
}) {
  const t = useT();
  const Icon = engine.local ? Cpu : Cloud;
  // The headline already names the model, so the line under it only adds
  // what is not on screen yet: the fallback behind a local engine, or why
  // the local engine is not the one in front.
  const detail = engine.local
    ? engine.fallback
      ? t("dictation.engine_fallback").replace("{0}", engine.fallback)
      : ""
    : engine.detail
      ? t("dictation.engine_local_off").replace("{0}", engine.detail)
      : "";

  return (
    <section
      className="rounded-xl border border-border bg-card p-5 shadow-rim"
      data-testid="dictation-engine"
    >
      <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">
        {t("dictation.engine_title")}
      </p>
      <div className="mt-3 flex items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-secondary text-foreground">
          <Icon aria-hidden="true" className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="text-base font-medium text-foreground-strong">
            {engine.local ? t("dictation.engine_on_device") : t("dictation.engine_cloud_short")}
          </p>
          <p className="truncate text-sm text-muted-foreground" title={engine.model || engine.provider}>
            {engine.model || engine.provider}
          </p>
        </div>
      </div>
      {detail && <p className="mt-3 text-sm text-muted-foreground">{detail}</p>}
    </section>
  );
}

/**
 * The one invitation on the screen: the dictionary is what makes names and
 * jargon come out right, and it lives one tab over, so the card is a door to
 * it rather than a copy of it.
 */
export function DictationVocabularyCard({ onOpen }: { onOpen: () => void }) {
  const t = useT();
  return (
    <section
      className="rounded-xl border border-border bg-card p-5 shadow-rim"
      data-testid="dictation-vocabulary"
    >
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-secondary text-foreground">
        <BookOpen aria-hidden="true" className="h-4 w-4" />
      </span>
      <h3 className="mt-3 text-base font-semibold text-foreground-strong">
        {t("dictation.vocab_title")}
      </h3>
      <p className="mt-1 text-sm text-muted-foreground">{t("dictation.vocab_body")}</p>
      <button
        type="button"
        onClick={onOpen}
        data-testid="dictation-open-dictionary"
        className="mt-4 inline-flex h-9 w-full items-center justify-center gap-2 rounded-md bg-primary px-4 text-base font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {t("dictation.vocab_cta")}
        <ArrowRight aria-hidden="true" className="h-4 w-4" />
      </button>
    </section>
  );
}
