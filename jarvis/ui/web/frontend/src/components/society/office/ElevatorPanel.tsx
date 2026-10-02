/**
 * The elevator's floor picker: pressing the call button (click, or E at the
 * doors) opens the elevator's button panel. A steel plate with the floor
 * indicator on top and one round push button per floor, top floor first,
 * each with the floor's name engraved beside it. The floor you are on shows
 * in the indicator and cannot be pressed. A pressed button lights its rim
 * for a moment, then the panel closes and the doors start to move.
 *
 * Keys: the floor's number (0, 1, 2) presses it, arrows move between the
 * buttons, Escape (or E) closes. It is a dialog, so the character stands
 * still while it is open (`ownsKeyboard`). Part of the diorama: one steel
 * palette in light and dark mode.
 */
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import { useT } from "@/i18n";
import { floorLevel, OFFICE_FLOORS, type OfficeFloor } from "./officeStore";

/** The floors as the panel lists them: top floor first, like a real elevator panel. */
export const PANEL_FLOORS: readonly OfficeFloor[] = [...OFFICE_FLOORS].reverse();

/** How long a pressed button stays lit before the panel hands the floor on. */
export const BUTTON_PRESS_MS = 280;

/** The floor a number key picks ("0" = ground floor), or null. */
export function floorForKey(key: string): OfficeFloor | null {
  const level = /^[0-9]$/.test(key) ? Number(key) : -1;
  return OFFICE_FLOORS[level] ?? null;
}

export function ElevatorPanel({ floor, onPick, onClose }: {
  floor: OfficeFloor;
  onPick: (floor: OfficeFloor) => void;
  onClose: () => void;
}) {
  const t = useT();
  const reduced = useReducedMotion() ?? false;
  const list = useRef<HTMLDivElement>(null);
  const [pressed, setPressed] = useState<OfficeFloor | null>(null);
  const pressTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(pressTimer.current), []);

  // A ref, so the capture-phase key listener always calls the current press without re-subscribing.
  const press = useRef<(target: OfficeFloor) => void>(() => undefined);
  press.current = (target: OfficeFloor) => {
    if (pressed || target === floor) return;
    setPressed(target);
    if (reduced) { onPick(target); return; }
    pressTimer.current = setTimeout(() => onPick(target), BUTTON_PRESS_MS);
  };

  // Focus the first floor you can go to, and give focus back to the office when the panel closes.
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
        if (!event.repeat) press.current(picked);
        return;
      }
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault(); event.stopPropagation();
        const buttons = [...(list.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        if (buttons.length === 0) return;
        const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
        buttons[at < 0 ? 0 : (at + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length].focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const up = pressed ? floorLevel(pressed) > floorLevel(floor) : null;
  return (
    <div className="office-lift-backdrop" data-office-ui onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="office-lift" role="dialog" data-state="open" aria-modal="true" aria-label={t("society.office.elevator_choose")}
        data-reduced={reduced || undefined}>
        <button type="button" className="office-lift-close" onClick={onClose} aria-label={t("society.office.close")}>×</button>
        <div className="office-lift-display" aria-hidden>
          <svg viewBox="0 0 24 24" className="office-lift-arrow" data-dir={up === null ? undefined : up ? "up" : "down"}>
            <path d="M12 4 L21 15 H3 Z" fill="currentColor" />
          </svg>
          <b>{floorLevel(floor)}</b>
        </div>
        <div ref={list} className="office-lift-rows">
          {PANEL_FLOORS.map((option) => {
            const here = option === floor;
            return (
              <button key={option} type="button" className="office-lift-row" disabled={here || (pressed !== null && pressed !== option)}
                aria-current={here ? "location" : undefined} aria-pressed={pressed === option}
                aria-keyshortcuts={String(floorLevel(option))} data-here={here || undefined} data-lit={pressed === option || undefined}
                onClick={() => press.current(option)}>
                <span className="office-lift-button" aria-hidden><span>{floorLevel(option)}</span></span>
                <span className="office-lift-label">{t(`society.office.floor_name_${option}`)}</span>
              </button>
            );
          })}
        </div>
        <p className="office-lift-keys">{t("society.office.elevator_keys")}</p>
      </div>
    </div>
  );
}
