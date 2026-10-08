/**
 * Portions adapted from pingdotgg/t3code @ 12069ee (apps/web
 * LoadBalancingSettings.tsx: a switch, then one share per machine, shown once
 * there is a second machine to balance against), MIT License, Copyright (c)
 * 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.
 *
 * "Automatic placement": when on, the new-workspace dialog offers
 * "Automatic", which sends the workspace to this PC or a switched-on, online
 * computer in proportion to each one's share (backend:
 * ``jarvis.computers.placement``).
 */
import { Laptop } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { usePlacement, useSetPlacement, useUpdateComputer } from "@/hooks/useComputers";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer } from "@/lib/computersApi";
import { MachineIcon } from "./machineKind";
import { Row, Section } from "./surface";

const LEVELS = [0, 1, 2, 3] as const;

function ShareControl({
  value,
  onChange,
  label,
  disabled,
  testId,
}: {
  value: number;
  onChange: (next: number) => void;
  label: string;
  disabled?: boolean;
  testId?: string;
}) {
  const t = useT();
  return (
    <div
      role="radiogroup"
      aria-label={label}
      data-testid={testId}
      className="inline-flex items-center gap-0.5 rounded-md border border-border p-0.5"
    >
      {LEVELS.map((level) => {
        const active = level === value;
        return (
          <button
            key={level}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(level)}
            className={cn(
              "h-7 rounded px-2.5 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              active ? "bg-secondary text-foreground-strong" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t(`computers.weight_${level}`)}
          </button>
        );
      })}
    </div>
  );
}

export function PlacementSection({ computers }: { computers: Computer[] }) {
  const t = useT();
  const settings = usePlacement();
  const save = useSetPlacement();
  const update = useUpdateComputer();
  const switchedOn = computers.filter((computer) => computer.enabled !== false);
  // One machine besides this PC is the least there is to balance between.
  if (switchedOn.length === 0 || !settings.data) return null;
  const enabled = settings.data.enabled;

  return (
    <Section title={t("computers.placement_title")} testId="computers-placement">
      <Row
        title={t("computers.placement_switch")}
        description={t("computers.placement_body")}
        control={
          <Switch
            checked={enabled}
            disabled={save.isPending}
            onCheckedChange={(next) => save.mutate({ enabled: next })}
            aria-label={t("computers.placement_switch")}
            data-testid="computers-placement-switch"
          />
        }
      />
      {enabled && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 sm:px-4">
            <span className="flex min-w-0 items-center gap-3">
              <Laptop aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate text-base font-medium text-foreground">{t("computers.placement_this_pc")}</span>
            </span>
            <ShareControl
              value={settings.data.local_weight}
              onChange={(next) => save.mutate({ local_weight: next })}
              label={t("computers.placement_this_pc")}
              disabled={save.isPending}
              testId="computers-placement-local"
            />
          </div>
          {switchedOn.map((computer) => (
            <div key={computer.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 sm:px-4">
              <span className="flex min-w-0 items-center gap-3">
                <MachineIcon computer={computer} aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="truncate text-base font-medium text-foreground">{computer.name}</span>
              </span>
              <ShareControl
                value={computer.placement_weight ?? 2}
                onChange={(next) => update.mutate({ id: computer.id, patch: { placement_weight: next } })}
                label={computer.name}
                disabled={update.isPending}
                testId={`computers-placement-${computer.id}`}
              />
            </div>
          ))}
        </>
      )}
    </Section>
  );
}
