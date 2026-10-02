/**
 * Riding the elevator, from inside the car. Pressing the call button puts the
 * person in the car: its steel side walls, lit ceiling and stone floor frame
 * the stage, and through the open doors they still see the floor they are on.
 * On the wall beside the doors hangs the car's operating panel (a directory
 * plate and one round push button per floor, plus "doors open" to step out
 * again); above the doors the floor indicator. Pressing a floor lights its
 * button, the doors close, the car pulls away with a slight jolt while the
 * indicator counts the floors, it settles, and the doors open on the new
 * floor — then the person steps out.
 *
 * OfficeStage owns the phases and their timing (the floor switches while the
 * doors are shut); this component draws the phase it is given. It is a modal
 * dialog the whole time, so the character stands still (`ownsKeyboard`).
 * Part of the diorama: one palette in light and dark mode. Reduced motion:
 * no stepping in or out, no jolt or counting.
 */
import { useEffect, useRef, useState } from "react";
import { useT } from "@/i18n";
import { floorLevel, OFFICE_FLOORS, type OfficeFloor } from "./officeStore";

export type CarPhase = "boarding" | "closing" | "riding" | "opening" | "leaving";

/** The car's floor buttons, top floor first, like the panel in a real car. */
export const CAR_BUTTONS: readonly OfficeFloor[] = [...OFFICE_FLOORS].reverse();

/** How long the indicator shows each floor while the car moves. */
export const RIDE_STEP_MS = 650;

/** The floor a number key picks ("0" = ground floor), or null. */
export function floorForKey(key: string): OfficeFloor | null {
  const level = /^[0-9]$/.test(key) ? Number(key) : -1;
  return OFFICE_FLOORS[level] ?? null;
}

/** The floor levels a ride passes, from where it starts to where it stops (both included). */
export function rideLevels(from: OfficeFloor, to: OfficeFloor): number[] {
  const a = floorLevel(from), b = floorLevel(to);
  const step = b >= a ? 1 : -1;
  const out: number[] = [];
  for (let level = a; level !== b + step; level += step) out.push(level);
  return out;
}

/** How long the car should at least be under way so the indicator can show every floor it passes. */
export function minRideMs(from: OfficeFloor, to: OfficeFloor): number {
  return Math.max(1, rideLevels(from, to).length - 1) * RIDE_STEP_MS;
}

function Arrow({ down }: { down: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="office-car-arrow" data-down={down || undefined} aria-hidden>
      <path d="M12 4 L21 15 H3 Z" fill="currentColor" />
    </svg>
  );
}

/** The "doors open" symbol: two arrows pointing away from a centre line. */
function DoorsOpenGlyph() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden>
      <path d="M11 12 L5 7 V17 Z M13 12 L19 7 V17 Z" fill="currentColor" />
      <rect x="11.4" y="5" width="1.2" height="14" fill="currentColor" />
    </svg>
  );
}

export function ElevatorCar({ phase, from, to, reduced, onPick, onLeave }: {
  phase: CarPhase;
  /** The floor the car is boarded on. */
  from: OfficeFloor;
  /** The floor pressed, once a button is pressed. */
  to: OfficeFloor | null;
  reduced: boolean;
  onPick: (floor: OfficeFloor) => void;
  onLeave: () => void;
}) {
  const t = useT();
  const panel = useRef<HTMLDivElement>(null);
  const boarding = phase === "boarding";
  const target = to ?? from;
  const levels = rideLevels(from, target);
  const up = floorLevel(target) > floorLevel(from);

  // The indicator: the boarded floor until the car moves, one floor per RIDE_STEP_MS under way, the stop on arrival.
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (reduced || phase !== "riding") return;
    const timer = window.setInterval(() => setStep((s) => Math.min(levels.length - 1, s + 1)), RIDE_STEP_MS);
    return () => window.clearInterval(timer);
  }, [phase, reduced, levels.length]);
  const index = phase === "opening" || phase === "leaving" || (reduced && phase === "riding") ? levels.length - 1
    : phase === "riding" ? step : 0;
  const level = levels[index] ?? floorLevel(from);

  // Step in with the focus on the first floor button you can press; hand focus back when stepping out.
  useEffect(() => {
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    panel.current?.querySelector<HTMLButtonElement>("button[data-floor]:not(:disabled)")?.focus({ preventScroll: true });
    return () => { before?.focus({ preventScroll: true }); };
  }, []);

  // Keys belong to the car while it is on screen (capture phase): a floor's number presses it, Escape or E steps out.
  const latest = useRef({ boarding, from, onPick, onLeave });
  latest.current = { boarding, from, onPick, onLeave };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const now = latest.current;
      if (event.key === "Escape" || event.code === "KeyE") {
        event.preventDefault(); event.stopPropagation();
        if (!event.repeat && now.boarding) now.onLeave();
        return;
      }
      const picked = event.ctrlKey || event.metaKey || event.altKey ? null : floorForKey(event.key);
      if (picked) {
        event.preventDefault(); event.stopPropagation();
        if (!event.repeat && now.boarding && picked !== now.from) now.onPick(picked);
        return;
      }
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault(); event.stopPropagation();
        const buttons = [...(panel.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        if (buttons.length === 0) return;
        const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
        buttons[at < 0 ? 0 : (at + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length].focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  const caption = to
    ? t(up ? "society.office.riding_up_to" : "society.office.riding_down_to").replace("{0}", t(`society.office.floor_name_${to}`))
    : t("society.office.elevator_choose");
  const moving = phase === "closing" || phase === "riding";
  return (
    <div className="office-car" data-phase={phase} data-direction={up ? "up" : "down"} data-reduced={reduced || undefined} data-office-ui
      role="dialog" data-state="open" aria-modal="true" aria-label={caption}>
      <div className="office-car-body">
        <i className="office-car-ceiling" aria-hidden><i className="office-car-light" /></i>
        <i className="office-car-floor" aria-hidden />
        <i className="office-car-wall" data-side="left" aria-hidden><i className="office-car-rail" /></i>
        <i className="office-car-wall" data-side="right" aria-hidden><i className="office-car-rail" /></i>
        <div className="office-car-front">
          <i className="office-car-lintel" aria-hidden />
          <i className="office-car-pier" data-side="left" aria-hidden />
          <i className="office-car-pier" data-side="right" aria-hidden />
          <div className="office-car-opening" aria-hidden>
            <i className="office-car-leaf" data-side="left" />
            <i className="office-car-leaf" data-side="right" />
          </div>
          <i className="office-car-frame" aria-hidden />
          <div className="office-car-display" aria-live="polite" aria-label={t(`society.office.floor_name_${OFFICE_FLOORS[level] ?? from}`)}>
            <span className="office-car-display-arrow" data-on={moving || undefined}><Arrow down={!up} /></span>
            <b key={level}>{level}</b>
          </div>
          <div ref={panel} className="office-car-panel" role="group" aria-label={t("society.office.elevator_choose")}>
            <ol className="office-car-directory">
              {CAR_BUTTONS.map((option) => (
                <li key={option}><b>{floorLevel(option)}</b><span>{t(`society.office.floor_name_${option}`)}</span></li>
              ))}
            </ol>
            {CAR_BUTTONS.map((option) => (
              <button key={option} type="button" className="office-car-button" data-floor={option}
                data-lit={to === option || undefined} aria-pressed={to === option}
                disabled={!boarding || option === from} aria-current={option === from ? "location" : undefined}
                aria-keyshortcuts={String(floorLevel(option))} aria-label={t(`society.office.floor_name_${option}`)}
                onClick={() => onPick(option)}>
                <span>{floorLevel(option)}</span>
              </button>
            ))}
            <i className="office-car-panel-gap" aria-hidden />
            <button type="button" className="office-car-button" data-kind="open" disabled={!boarding}
              aria-label={t("society.office.elevator_leave")} onClick={onLeave}>
              <span><DoorsOpenGlyph /></span>
            </button>
          </div>
        </div>
      </div>
      {boarding && <p className="office-car-hint">{t("society.office.elevator_keys")}</p>}
    </div>
  );
}
