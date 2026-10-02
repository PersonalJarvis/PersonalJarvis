/**
 * The elevator's floor picker: pressing the call button (click, or E at the
 * doors) opens this small panel with one button per floor, top floor first
 * like a real elevator panel. The current floor is shown but not pickable.
 * Keys: the floor's number (0, 1, 2) picks it, arrows move between buttons,
 * Escape closes. It is a dialog, so the character stands still while it is
 * open (`ownsKeyboard`).
 */
import { useEffect, useRef } from "react";
import { useT } from "@/i18n";
import { floorLevel, OFFICE_FLOORS, type OfficeFloor } from "./officeStore";

/** The floors as the panel lists them: top floor first. */
export const PANEL_FLOORS: readonly OfficeFloor[] = [...OFFICE_FLOORS].reverse();

/** The floor a number key picks ("0" = ground floor), or null. */
export function floorForKey(key: string): OfficeFloor | null {
  const level = /^[0-9]$/.test(key) ? Number(key) : -1;
  return OFFICE_FLOORS[level] ?? null;
}

export function ElevatorPanel({ floor, counts, onPick, onClose }: {
  floor: OfficeFloor;
  /** A line under each floor's name (e.g. how many agents work there); null leaves it out. */
  counts: Record<OfficeFloor, string | null>;
  onPick: (floor: OfficeFloor) => void;
  onClose: () => void;
}) {
  const t = useT();
  const list = useRef<HTMLDivElement>(null);

  // Focus the nearest floor you can go to, and give focus back to the office when the panel closes.
  useEffect(() => {
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    list.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus({ preventScroll: true });
    return () => { before?.focus({ preventScroll: true }); };
  }, []);

  // Capture phase: the office's own E, Escape and movement keys never see these.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" || event.code === "KeyE") {
        event.preventDefault(); event.stopPropagation();
        if (!event.repeat) onClose();
        return;
      }
      // Ctrl/Alt/Cmd + a digit belongs to the browser or the app, never to the panel.
      const picked = event.ctrlKey || event.metaKey || event.altKey ? null : floorForKey(event.key);
      if (picked) {
        event.preventDefault(); event.stopPropagation();
        if (picked !== floor && !event.repeat) onPick(picked);
        return;
      }
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault(); event.stopPropagation();
        const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        if (buttons.length === 0) return;
        const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
        const next = at < 0 ? 0 : (at + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
        buttons[next].focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [floor, onClose, onPick]);

  return (
    <div className="office-lift-backdrop" data-office-ui onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="office-card office-lift" role="dialog" data-state="open" aria-modal="true" aria-labelledby="office-lift-title">
        <div className="office-lift-head">
          <h2 id="office-lift-title">{t("society.office.elevator_choose")}</h2>
          <button type="button" className="office-icon-button" onClick={onClose} aria-label={t("society.office.close")}>×</button>
        </div>
        <div ref={list} className="office-lift-floors">
          {PANEL_FLOORS.map((target) => {
            const here = target === floor;
            const count = counts[target];
            return (
              <button key={target} type="button" className="office-lift-floor" disabled={here} aria-current={here ? "location" : undefined}
                aria-keyshortcuts={String(floorLevel(target))} data-floor={target} onClick={() => onPick(target)}>
                <span className="office-lift-number" aria-hidden>{floorLevel(target)}</span>
                <span className="office-lift-text">
                  <b>{t(`society.office.floor_name_${target}`)}</b>
                  <small>{here ? t("society.office.elevator_here") : count ?? t(`society.office.floor_about_${target}`)}</small>
                </span>
              </button>
            );
          })}
        </div>
        <p className="office-lift-keys">{t("society.office.elevator_keys")}</p>
      </div>
    </div>
  );
}
