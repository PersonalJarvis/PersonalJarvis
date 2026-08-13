/**
 * One device, with only the controls it can actually honour.
 *
 * The controls are derived from `device.commands` — the list the BACKEND
 * computed from the device's capabilities — rather than from its kind. That is
 * what makes a non-dimmable bulb and a dimmable one look different without a
 * single vendor branch in this file, and it is why adding a capability stays a
 * backend-only change.
 *
 * Colours come from theme tokens throughout (`bg-card`, `border-border`,
 * `text-primary`, `text-muted-foreground`), so the card is correct in light and
 * dark mode with no per-mode table — the repo's frontend theming rule.
 */
import { useEffect, useState } from "react";
import { Loader2, WifiOff } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import type { CommandOutcome, SmartDevice } from "@/hooks/useSmartHome";
import {
  iconForKind,
  isActive,
  renderStateLine,
  stateLine,
} from "@/views/smarthome/deviceMeta";

export interface DeviceCardProps {
  device: SmartDevice;
  onCommand: (
    deviceId: string,
    command: string,
    args?: Record<string, unknown>,
    optimistic?: Record<string, unknown>,
    confirm?: boolean,
  ) => Promise<CommandOutcome>;
  /** Room name is redundant on the Rooms tab, where the heading already says it. */
  showRoom?: boolean;
}

export function DeviceCard({ device, onCommand, showRoom = true }: DeviceCardProps) {
  const t = useT();
  const Icon = iconForKind(device.kind);
  const active = isActive(device);
  const line = stateLine(device);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  // Which command the server asked us to confirm, if any. The SERVER decides
  // that a command is irreversible; this component only asks the question and
  // resends. Keeping the list on one side is what stops the front-door guard
  // from drifting out of sync with the button that opens it.
  const [awaitingConfirm, setAwaitingConfirm] = useState<string | null>(null);

  // A refusal is a message about the last action, not a property of the device.
  // Clearing it on the next state change is what keeps a stale "that lamp
  // cannot do colour" from sitting under a card the user has since fixed.
  useEffect(() => {
    setRefusal(null);
    setAwaitingConfirm(null);
  }, [device.state]);

  const can = (command: string) => device.commands.includes(command);

  const run = async (
    command: string,
    args?: Record<string, unknown>,
    optimistic?: Record<string, unknown>,
  ) => {
    // Second click on a command the server asked about: now it means yes.
    const confirming = awaitingConfirm === command;
    setBusy(true);
    setRefusal(null);
    const outcome = await onCommand(device.id, command, args, optimistic, confirming);
    if (outcome.requires_confirmation) {
      setAwaitingConfirm(command);
    } else {
      setAwaitingConfirm(null);
      if (!outcome.ok) setRefusal(outcome.error ?? t("smarthome.command_failed"));
    }
    setBusy(false);
  };

  const onOff = typeof device.state.on_off === "boolean" ? device.state.on_off : null;
  const brightness =
    typeof device.state.brightness === "number" ? device.state.brightness : null;
  const position = typeof device.state.position === "number" ? device.state.position : null;
  const target =
    typeof device.state.target_temperature === "number"
      ? device.state.target_temperature
      : null;
  const locked = typeof device.state.locked === "boolean" ? device.state.locked : null;

  return (
    <div
      data-testid={`smarthome-device-${device.id}`}
      data-active={active ? "true" : "false"}
      className={cn(
        "flex flex-col gap-3 rounded-xl border bg-card p-4 text-card-foreground transition-colors",
        active ? "border-primary/40" : "border-border",
        !device.reachable && "opacity-55",
      )}
    >
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-colors",
            active
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-border bg-secondary/50 text-muted-foreground",
          )}
        >
          <Icon className="h-4 w-4" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium">{device.name}</span>
            {!device.reachable && (
              <WifiOff
                className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                aria-label={t("smarthome.unreachable")}
              />
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">
            {!device.reachable
              ? t("smarthome.unreachable")
              : [showRoom ? device.room : null, line ? renderStateLine(t, line) : null]
                  .filter(Boolean)
                  .join(" · ")}
          </p>
        </div>
        {busy ? (
          <Loader2
            className="h-4 w-4 shrink-0 animate-spin text-muted-foreground"
            aria-hidden
          />
        ) : (
          onOff !== null &&
          can("turn_on") && (
            <Switch
              checked={onOff}
              disabled={!device.reachable}
              aria-label={device.name}
              onCheckedChange={(next) =>
                void run(next ? "turn_on" : "turn_off", {}, { on_off: next })
              }
            />
          )
        )}
      </div>

      {device.reachable && can("set_brightness") && brightness !== null && (
        <SliderRow
          label={t("smarthome.control.brightness")}
          value={brightness}
          suffix="%"
          onCommit={(value) =>
            void run(
              "set_brightness",
              { brightness: value },
              { brightness: value, on_off: value > 0 },
            )
          }
        />
      )}

      {device.reachable && can("set_position") && position !== null && (
        <SliderRow
          label={t("smarthome.control.position")}
          value={position}
          suffix="%"
          onCommit={(value) =>
            void run(
              "set_position",
              { position: value },
              { position: value, open: value > 0 },
            )
          }
        />
      )}

      {device.reachable && can("set_temperature") && target !== null && (
        <StepperRow
          label={t("smarthome.control.temperature")}
          value={target}
          unit={device.unit ?? "°C"}
          step={0.5}
          min={5}
          max={30}
          onCommit={(value) =>
            void run(
              "set_temperature",
              { temperature: value },
              { target_temperature: value },
            )
          }
        />
      )}

      {device.reachable && locked !== null && can("lock") && (
        <div className="flex gap-2">
          <SmallButton
            active={locked}
            onClick={() => void run("lock", {}, { locked: true })}
            label={t("smarthome.control.lock")}
          />
          {/* Unlocking is the one command in the section a person may not be
              able to undo from where they are standing, so it is a deliberate
              second button rather than the other half of a toggle — and the
              server turns the first click into a question. */}
          <SmallButton
            active={!locked}
            onClick={() => void run("unlock", {}, { locked: false })}
            label={
              awaitingConfirm === "unlock"
                ? t("smarthome.control.confirm")
                : t("smarthome.control.unlock")
            }
            danger
            testId={`smarthome-unlock-${device.id}`}
          />
        </div>
      )}

      {device.reachable && can("open") && can("close") && position === null && (
        <div className="flex gap-2">
          <SmallButton
            onClick={() => void run("open", {}, { open: true })}
            label={
              awaitingConfirm === "open"
                ? t("smarthome.control.confirm")
                : t("smarthome.control.open")
            }
          />
          <SmallButton
            onClick={() => void run("close", {}, { open: false })}
            label={t("smarthome.control.close")}
          />
        </div>
      )}

      {device.reachable && can("activate") && (
        <SmallButton
          onClick={() => void run("activate")}
          label={t("smarthome.control.activate")}
          full
        />
      )}

      {refusal && (
        <p
          data-testid={`smarthome-refusal-${device.id}`}
          className="text-xs text-destructive"
        >
          {refusal}
        </p>
      )}
    </div>
  );
}

/**
 * A slider that only sends on release.
 *
 * Sending on every drag frame would fire dozens of requests at a hub over the
 * home network for one gesture — and hubs rate-limit, so the lamp would end up
 * wherever the last accepted request left it rather than where the finger
 * stopped. The local value keeps the handle responsive in the meantime.
 */
function SliderRow({
  label,
  value,
  suffix,
  onCommit,
}: {
  label: string;
  value: number;
  suffix?: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    if (!dragging) setDraft(value);
  }, [value, dragging]);

  return (
    <label className="flex items-center gap-3 text-xs text-muted-foreground">
      <span className="w-16 shrink-0">{label}</span>
      <input
        type="range"
        min={0}
        max={100}
        value={draft}
        aria-label={label}
        className="h-1 flex-1 cursor-pointer appearance-none rounded-full bg-muted-foreground/30 accent-primary"
        onChange={(e) => {
          setDragging(true);
          setDraft(Number(e.target.value));
        }}
        onPointerUp={() => {
          setDragging(false);
          onCommit(draft);
        }}
        onKeyUp={() => {
          setDragging(false);
          onCommit(draft);
        }}
      />
      <span className="w-10 shrink-0 text-right tabular-nums text-foreground">
        {draft}
        {suffix}
      </span>
    </label>
  );
}

function StepperRow({
  label,
  value,
  unit,
  step,
  min,
  max,
  onCommit,
}: {
  label: string;
  value: number;
  unit: string;
  step: number;
  min: number;
  max: number;
  onCommit: (value: number) => void;
}) {
  const clamp = (next: number) =>
    Math.round(Math.min(max, Math.max(min, next)) * 2) / 2;
  return (
    <div className="flex items-center gap-3 text-xs text-muted-foreground">
      <span className="w-16 shrink-0">{label}</span>
      <div className="flex flex-1 items-center justify-end gap-2">
        <StepButton label="−" onClick={() => onCommit(clamp(value - step))} />
        <span className="w-16 text-center text-sm tabular-nums text-foreground">
          {value}
          {unit}
        </span>
        <StepButton label="+" onClick={() => onCommit(clamp(value + step))} />
      </div>
    </div>
  );
}

function StepButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-secondary/50 text-sm text-foreground transition-colors hover:border-primary/40 hover:text-primary"
    >
      {label}
    </button>
  );
}

function SmallButton({
  label,
  onClick,
  active = false,
  danger = false,
  full = false,
  testId,
}: {
  label: string;
  onClick: () => void;
  active?: boolean;
  danger?: boolean;
  full?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
        full ? "w-full" : "flex-1",
        active
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border bg-secondary/50 text-muted-foreground hover:text-foreground",
        danger && !active && "hover:border-destructive/50 hover:text-destructive",
      )}
    >
      {label}
    </button>
  );
}
