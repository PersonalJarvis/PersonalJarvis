/**
 * The elevator's floor picker: pressing the call button (click, or E at the
 * doors) opens this panel, built like a real elevator's control panel: a
 * display with the current floor on top, then one round, backlit key per
 * floor, top floor first. The current floor's key glows but cannot be
 * picked. A picked key lights up for a moment before the panel closes and
 * the doors start to move.
 *
 * Keys: the floor's number (0, 1, 2) picks it, arrows move between keys,
 * Escape (or E) closes. It is a dialog, so the character stands still while
 * it is open (`ownsKeyboard`). Part of the diorama: one steel palette in
 * light and dark mode.
 */
import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";
import { useT } from "@/i18n";
import { floorLevel, OFFICE_FLOORS, type OfficeFloor } from "./officeStore";

/** The floors as the panel lists them: top floor first. */
export const PANEL_FLOORS: readonly OfficeFloor[] = [...OFFICE_FLOORS].reverse();

/** How long a pressed key stays lit before the panel hands the floor on. */
export const KEY_PRESS_MS = 260;

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
  const reduced = useReducedMotion() ?? false;
  const list = useRef<HTMLDivElement>(null);
  const [pressed, setPressed] = useState<OfficeFloor | null>(null);
  const pressTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(pressTimer.current), []);

  // Light the key, then hand the floor on; one press per panel.
  // A ref, so the capture-phase key listener always calls the current press without re-subscribing.
  const press = useRef<(target: OfficeFloor) => void>(() => undefined);
  press.current = (target: OfficeFloor) => {
    if (pressed || target === floor) return;
    setPressed(target);
    if (reduced) { onPick(target); return; }
    pressTimer.current = setTimeout(() => onPick(target), KEY_PRESS_MS);
  };

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
        if (!event.repeat) press.current(picked);
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
  }, [onClose]);

  const target = pressed ?? floor;
  const up = floorLevel(target) > floorLevel(floor);
  return (
    <div className="office-lift-backdrop" data-office-ui onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="office-lift" role="dialog" data-state="open" aria-modal="true" aria-labelledby="office-lift-title" data-reduced={reduced || undefined}>
        <div className="office-lift-top">
          <div className="office-lift-display" aria-hidden>
            <svg viewBox="0 0 24 24" width="14" height="14" className="office-lift-display-arrow" data-dir={pressed ? (up ? "up" : "down") : "idle"}>
              <path d="M12 5 L20 15 H4 Z" fill="currentColor" />
            </svg>
            <b>{floorLevel(floor)}</b>
          </div>
          <h2 id="office-lift-title">{t("society.office.elevator_choose")}</h2>
          <button type="button" className="office-lift-close" onClick={onClose} aria-label={t("society.office.close")}>×</button>
        </div>
        <div ref={list} className="office-lift-floors">
          {PANEL_FLOORS.map((option) => {
            const here = option === floor;
            const count = counts[option];
            return (
              <button key={option} type="button" className="office-lift-floor" disabled={here || (pressed !== null && pressed !== option)}
                aria-current={here ? "location" : undefined} aria-pressed={pressed === option || undefined}
                aria-keyshortcuts={String(floorLevel(option))} data-floor={option} data-here={here || undefined} data-pressed={pressed === option || undefined}
                onClick={() => press.current(option)}>
                <span className="office-lift-key" aria-hidden><span>{floorLevel(option)}</span></span>
                <span className="office-lift-text">
                  <b>{t(`society.office.floor_name_${option}`)}</b>
                  <small>{here ? t("society.office.elevator_here") : count ?? t(`society.office.floor_about_${option}`)}</small>
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
