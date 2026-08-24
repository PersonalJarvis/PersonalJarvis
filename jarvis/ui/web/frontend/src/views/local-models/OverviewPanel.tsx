/**
 * Overview — the Simple-mode front page of the Local models section.
 *
 * Four headline tiles (server state, graphics memory, disk used by models,
 * what is loaded right now) over the Roles ledger. The tiles read `GET server`
 * and the shortlist's accelerator facts; nothing here writes. The roles part
 * is `RolesPanel`, mounted underneath so the integration wires one component.
 *
 * Props take `providerId` — the id of the card that declares
 * `supports_model_pull` — never a provider name.
 */
import { Cpu, HardDrive, Layers, Server } from "lucide-react";

import { StatTile } from "@/components/extensions/primitives";
import { useCatalogRecommended, useServer } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";

import { RolesPanel, type RolesPanelProps } from "./RolesPanel";

export type OverviewPanelProps = RolesPanelProps;

/** Bytes to a short gigabyte figure ("12.4 GB"); "0 GB" below a megabyte. */
export function formatGigabytes(bytes: number): string {
  const gb = bytes / 1024 ** 3;
  if (gb >= 10) return `${gb.toFixed(0)} GB`;
  if (gb >= 0.1) return `${gb.toFixed(1)} GB`;
  return bytes > 0 ? `${(bytes / 1024 ** 2).toFixed(0)} MB` : "0 GB";
}

export function OverviewPanel({
  providerId,
  onTune,
  onOpenApiKeys,
}: OverviewPanelProps) {
  const t = useT();
  const server = useServer(providerId);
  const shortlist = useCatalogRecommended(providerId);

  const s = server.data;
  const remote = s?.host_kind === "remote";
  let serverValue = t("local_models.overview.server_unknown");
  let serverHint: string = s?.detail ?? "";
  let serverTone: "ok" | "warn" | "danger" | "primary" | "success" = "primary";
  if (s) {
    if (s.running) {
      serverValue = remote
        ? t("local_models.overview.server_remote")
        : t("local_models.overview.server_running");
      serverHint = [
        s.version
          ? fill(t("local_models.overview.server_version"), {
              version: s.version,
            })
          : "",
        s.base_url,
      ]
        .filter(Boolean)
        .join(" · ");
      serverTone = "success";
    } else if (remote) {
      serverValue = t("local_models.overview.server_unreachable");
      serverHint = s.error ?? s.detail ?? s.base_url;
      serverTone = "warn";
    } else if (!s.installed) {
      serverValue = t("local_models.overview.server_not_installed");
      serverTone = "warn";
    } else {
      serverValue = t("local_models.overview.server_stopped");
      serverTone = "warn";
    }
  } else if (server.isError) {
    serverValue = t("local_models.overview.server_unreachable");
    serverHint =
      server.error instanceof Error
        ? server.error.message
        : String(server.error);
    serverTone = "danger";
  }

  const acceleratorGb = shortlist.data?.accelerator_gb ?? 0;
  const acceleratorSource = shortlist.data?.accelerator_source ?? "";
  const gpuValue =
    acceleratorGb > 0
      ? `${acceleratorGb.toFixed(1)} GB`
      : t("local_models.overview.gpu_unknown");
  const gpuHint =
    acceleratorGb > 0 && acceleratorSource
      ? fill(t("local_models.overview.gpu_source"), {
          source: acceleratorSource,
        })
      : t("local_models.overview.gpu_unknown_hint");

  const loaded = s?.running_models ?? [];
  const loadedHint =
    loaded.length > 0
      ? loaded.map((m) => m.name).join(", ")
      : t("local_models.overview.loaded_none");

  return (
    <div className="space-y-4" data-testid="local-models-overview-panel">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatTile
          icon={<Server className="h-3.5 w-3.5" />}
          label={t("local_models.overview.server")}
          value={serverValue}
          hint={serverHint}
          tone={serverTone}
          loading={server.isLoading}
        />
        <StatTile
          icon={<Cpu className="h-3.5 w-3.5" />}
          label={t("local_models.overview.gpu")}
          value={gpuValue}
          hint={gpuHint}
          tone="primary"
          loading={shortlist.isLoading}
        />
        <StatTile
          icon={<HardDrive className="h-3.5 w-3.5" />}
          label={t("local_models.overview.disk")}
          value={formatGigabytes(s?.disk_bytes ?? 0)}
          hint={s?.models_dir ?? ""}
          tone="primary"
          loading={server.isLoading}
        />
        <StatTile
          icon={<Layers className="h-3.5 w-3.5" />}
          label={t("local_models.overview.loaded")}
          value={String(loaded.length)}
          hint={
            loaded.length > 0
              ? `${loadedHint} · ${formatGigabytes(s?.loaded_vram_bytes ?? 0)}`
              : loadedHint
          }
          tone="primary"
          loading={server.isLoading}
        />
      </div>

      <RolesPanel
        providerId={providerId}
        onTune={onTune}
        onOpenApiKeys={onOpenApiKeys}
      />
    </div>
  );
}
