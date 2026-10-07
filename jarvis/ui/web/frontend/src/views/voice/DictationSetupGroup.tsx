import { ArrowRight, BookOpen, Cloud, Cpu } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DictationStatus } from "@/hooks/useDictation";
import { useT } from "@/i18n";
import { VoiceGroup, VoiceRow, VoiceTag } from "@/views/voice/voiceUi";

/**
 * Two short rows under the numbers: which recognizer answers the next press,
 * and the door to the dictionary that makes names and jargon come out right.
 */
export function DictationSetupGroup({
  engine,
  dictionaryCount,
  onOpenDictionary,
}: {
  /** Omitted while dictation is unavailable or the backend names no engine. */
  engine?: NonNullable<DictationStatus["engine"]> | null;
  /** Entries in the dictionary; null while unknown (loading or failed). */
  dictionaryCount: number | null;
  onOpenDictionary: () => void;
}) {
  const t = useT();
  return (
    <VoiceGroup testId="dictation-setup">
      {engine && <EngineRow engine={engine} />}
      <VoiceRow
        testId="dictation-vocabulary"
        icon={<BookOpen />}
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            {t("dictation.vocab_title")}
            {dictionaryCount !== null && dictionaryCount > 0 && (
              <VoiceTag>
                <span className="tabular-nums" data-testid="dictation-vocab-count">
                  {dictionaryCount === 1
                    ? t("dictation.vocab_count_one")
                    : t("dictation.vocab_count_other").replace(
                        "{0}",
                        dictionaryCount.toLocaleString(),
                      )}
                </span>
              </VoiceTag>
            )}
          </span>
        }
        description={t("dictation.vocab_body")}
        control={
          <Button
            size="sm"
            variant="outline"
            onClick={onOpenDictionary}
            data-testid="dictation-open-dictionary"
          >
            {t("dictation.vocab_cta")}
            <ArrowRight aria-hidden="true" />
          </Button>
        }
      />
    </VoiceGroup>
  );
}

/**
 * Which recognizer really answers the next press (P-41): the settings name one,
 * the lane may hold another, and this row says which — where it runs as the
 * tag, the exact model under the name, and the fallback or the reason the local
 * engine is not in front under that.
 */
function EngineRow({ engine }: { engine: NonNullable<DictationStatus["engine"]> }) {
  const t = useT();
  const Icon = engine.local ? Cpu : Cloud;
  const detail = engine.local
    ? engine.fallback
      ? t("dictation.engine_fallback").replace("{0}", engine.fallback)
      : ""
    : engine.detail
      ? t("dictation.engine_local_off").replace("{0}", engine.detail)
      : "";
  const model = engine.model || engine.provider;

  return (
    <VoiceRow
      testId="dictation-engine"
      icon={<Icon />}
      title={t("dictation.engine_title")}
      description={
        <>
          <span className="block truncate" title={model}>
            {model}
          </span>
          {detail && <span className="mt-0.5 block">{detail}</span>}
        </>
      }
      control={
        <VoiceTag>
          {engine.local ? t("dictation.engine_on_device") : t("dictation.engine_cloud_short")}
        </VoiceTag>
      }
    />
  );
}
