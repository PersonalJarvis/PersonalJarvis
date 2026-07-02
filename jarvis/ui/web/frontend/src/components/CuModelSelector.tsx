import { useEffect, useState } from "react";
import { BrainModelSelector } from "@/components/BrainModelSelector";
import { getCuModel, saveCuModel } from "@/hooks/useProviders";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * Per-provider "Computer-Use model" picker (Phase 3).
 *
 * Computer-Use runs on the provider's main model by default; this lets the user
 * pin a different (e.g. stronger) model just for CU, without affecting chat and
 * without any automatic escalation. It reuses the shared {@link BrainModelSelector}
 * for the searchable dropdown (same live catalog as the main model picker) and
 * adds a "use my main model" reset. An empty selection means "use my main model".
 */
export function CuModelSelector({
  providerId,
  recommendedModel,
}: {
  providerId: string;
  recommendedModel?: string | null;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const [cuModel, setCuModel] = useState<string>("");
  const [usesMain, setUsesMain] = useState<boolean>(true);
  const [effective, setEffective] = useState<string>("");
  const [clearing, setClearing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await getCuModel(providerId);
        if (cancelled) return;
        setCuModel(r.cu_model ?? "");
        setUsesMain(r.uses_main ?? !r.cu_model);
        setEffective(r.effective_model ?? "");
      } catch {
        /* leave defaults — the row degrades to "using main" */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [providerId]);

  async function clearToMain() {
    setClearing(true);
    try {
      await saveCuModel(providerId, "");
      setCuModel("");
      setUsesMain(true);
      pushToast("success", t("apikeys_cu_model.cleared"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className="space-y-1">
      <BrainModelSelector
        providerId={providerId}
        currentModel={cuModel}
        controlled
        visionOnly
        recommendedModel={recommendedModel}
        headingLabel={t("apikeys_cu_model.heading")}
        placeholder={t("apikeys_cu_model.use_main")}
        onSave={async (model) => {
          const res = await saveCuModel(providerId, model);
          setCuModel(model);
          setUsesMain(!model);
          setEffective(model || effective);
          return res;
        }}
      />
      <p className="text-[10px] text-muted-foreground/80">
        {t("apikeys_cu_model.vision_note")}
      </p>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] text-muted-foreground">
          {usesMain
            ? t("apikeys_cu_model.using_main")
            : `${t("apikeys_cu_model.effective")}: ${effective || cuModel}`}
        </span>
        {!usesMain && (
          <button
            type="button"
            data-testid="cu-use-main"
            onClick={() => void clearToMain()}
            disabled={clearing}
            className="text-[11px] text-muted-foreground underline hover:text-foreground"
          >
            {t("apikeys_cu_model.use_main")}
          </button>
        )}
      </div>
    </div>
  );
}
