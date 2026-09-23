import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel, PanelHeader } from "@/components/extensions/primitives";
import { useNativeVoiceModels } from "@/hooks/useNativeVoiceModels";
import { useT } from "@/i18n";

export function NativeVoicePanel() {
  const t = useT();
  const { library, acquire, cancel } = useNativeVoiceModels();
  const [manifest, setManifest] = useState<unknown>();
  const [source, setSource] = useState("");
  const [invalidFile, setInvalidFile] = useState(false);
  const fileVersion = useRef(0);
  const active = library.data?.active_job;
  const busy = Boolean(active) || acquire.isPending;
  const error = library.isError || acquire.isError || cancel.isError;

  return (
    <div className="space-y-4" data-testid="native-voice-panel">
      <Panel className="space-y-3 p-5">
        <PanelHeader title={t("local_models.native.title")} subtitle={t("local_models.native.subtitle")} />
        <p className="text-sm text-muted-foreground">{t("local_models.native.preview")}</p>
        <Button variant="outline" onClick={() => void library.refetch()} disabled={library.isFetching}>
          {t("local_models.reload")}
        </Button>
        {library.isPending && <p role="status">{t("local_models.loading")}</p>}
        {error && <p role="alert" className="text-sm text-destructive">{t("local_models.native.error")}</p>}
      </Panel>
      {library.data?.entries.map((model) => {
        const job = model.job;
        const running = job?.phase === "acquiring" || job?.phase === "cancelling";
        const progress = job ? Math.min(100, Math.floor(100 * job.completed_bytes / Math.max(1, job.total_bytes))) : 0;
        return (
          <Panel key={model.fingerprint} className="space-y-3 p-5">
            <h3 className="font-medium">{model.label}</h3>
            <p className="text-sm text-muted-foreground">
              {(model.download_bytes / 1_000_000_000).toFixed(2)} GB · {model.languages.join(", ")}
            </p>
            <p className="text-sm" role="status">
              {running ? `${t(`local_models.native.${job.phase}`)} ${progress}%`
                : job?.phase === "failed" || job?.phase === "cancelled" ? t(`local_models.native.${job.phase}`)
                : model.package_present ? t("local_models.native.stored") : t("local_models.native.missing")}
            </p>
            {running && <progress className="w-full accent-primary" aria-label={model.label} value={progress} max={100} />}
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" disabled={busy || library.isPending}
                onClick={() => acquire.mutate({ fingerprint: model.fingerprint })}>
                {model.package_present ? t("local_models.native.verify") : t("local_models.native.download")}
              </Button>
              {running && job && <Button variant="outline" disabled={cancel.isPending || job.phase === "cancelling"}
                onClick={() => cancel.mutate(job.id)}>{t("local_models.native.cancel")}</Button>}
            </div>
          </Panel>
        );
      })}
      <Panel className="space-y-3 p-5">
        <PanelHeader title={t("local_models.native.custom")} subtitle={t("local_models.native.custom_help")} />
        <label className="block space-y-2 text-sm">
          <span>{t("local_models.native.manifest")}</span>
          <Input type="file" accept=".json,application/json" disabled={busy} onChange={async (event) => {
            const version = ++fileVersion.current;
            const file = event.target.files?.[0];
            setManifest(undefined);
            setInvalidFile(false);
            if (!file) return;
            try {
              if (file.size > 1_048_576) throw new Error("Manifest too large");
              const value: unknown = JSON.parse(await file.text());
              if (version !== fileVersion.current) return;
              if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid manifest");
              setManifest(value);
            } catch {
              if (version === fileVersion.current) setInvalidFile(true);
            }
          }} />
        </label>
        <label className="block space-y-2 text-sm">
          <span>{t("local_models.native.folder")}</span>
          <Input value={source} onChange={(event) => setSource(event.target.value)} disabled={busy} />
        </label>
        {invalidFile && <p role="alert" className="text-sm text-destructive">{t("local_models.native.invalid")}</p>}
        <Button disabled={busy || manifest === undefined} onClick={() => acquire.mutate({ manifest, source_directory: source })}>
          {t("local_models.native.import")}
        </Button>
      </Panel>
    </div>
  );
}
