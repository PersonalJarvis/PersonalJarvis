/**
 * The level system's HUD pieces over the Verse: the person's level card (a
 * ring that fills with XP, the title, a bar with a trailing "ghost" that
 * catches up after each gain), the pet's line under it, the level chip worn
 * on name plates, and the toasts for agents that levelled up.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "@/i18n";
import { equippedFor, levelFraction } from "./cosmetics";
import { FRAME_STYLE, type SubjectKind } from "./levelCatalog";
import { PERSON_SUBJECT, petSubject } from "./progressionApi";
import { useProgression, type Celebration } from "./progressionStore";

const TOAST_MS = 6500;

/** The frame a subject's chip wears: its chosen or best unlocked frame. */
export function useFrameStyle(kind: SubjectKind, level: number) {
  const rewards = useProgression((s) => s.snapshot?.rewards);
  const choice = useProgression((s) => (kind === "agent" ? undefined : s.choices[kind]?.frame));
  return useMemo(() => {
    const frame = rewards ? equippedFor(rewards, kind, level, choice ? { frame: choice } : {}).frame : undefined;
    return FRAME_STYLE[(frame ?? "frame_none") as keyof typeof FRAME_STYLE];
  }, [rewards, kind, level, choice]);
}

/** The small hexagon level number on a name plate. */
export function LevelChip({ kind, level }: { kind: SubjectKind; level: number }) {
  const t = useT();
  const style = useFrameStyle(kind, level);
  return (
    <span className="level-chip" title={t("society.level.level_n").replace("{0}", String(level))}
      style={{ background: style.fill, color: style.ink, boxShadow: `inset 0 0 0 1.5px ${style.ring}` }}>
      {level}
    </span>
  );
}

/** A bar whose gain lights up first and fills a beat later, so every gain reads as motion. */
export function XpBar({ fraction, kind, label }: { fraction: number; kind: SubjectKind; label: string }) {
  const [ghost, setGhost] = useState(fraction);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    clearTimeout(timer.current);
    // After a level-up the bar restarts below the ghost: the ghost resets with it.
    if (fraction < ghost) { setGhost(fraction); return; }
    timer.current = setTimeout(() => setGhost(fraction), 450);
    return () => clearTimeout(timer.current);
  }, [fraction, ghost]);
  return (
    <span className="level-bar" data-kind={kind} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={Math.round(fraction * 100)}>
      {/* The gain shows at once in a bright segment; the solid fill slides up to it a beat later. */}
      <i className="level-bar-gain" style={{ width: `${fraction * 100}%` }} />
      <i className="level-bar-fill" style={{ width: `${Math.min(fraction, ghost) * 100}%` }} />
    </span>
  );
}

export function LevelRing({ kind, level, fraction, size = 44 }: { kind: SubjectKind; level: number; fraction: number; size?: number }) {
  const style = useFrameStyle(kind, level);
  const r = 18, c = 2 * Math.PI * r;
  return (
    <span className="level-ring" style={{ width: size, height: size }} data-kind={kind}>
      <svg viewBox="0 0 44 44" width={size} height={size} aria-hidden>
        <circle cx="22" cy="22" r={r} className="level-ring-track" />
        <circle cx="22" cy="22" r={r} className="level-ring-fill" strokeDasharray={`${c * fraction} ${c}`} transform="rotate(-90 22 22)" />
        <circle cx="22" cy="22" r="14" fill={style.fill} stroke={style.ring} strokeWidth="2" />
      </svg>
      <b style={{ color: style.ink }}>{level}</b>
    </span>
  );
}

export function LevelHud({ playerName, petName, compact }: { playerName: string; petName: string; compact: boolean }) {
  const t = useT();
  const person = useProgression((s) => s.subjects[PERSON_SUBJECT]);
  const petId = useProgression((s) => s.petId);
  const pet = useProgression((s) => s.subjects[petSubject(petId)]);
  const loaded = useProgression((s) => !!s.snapshot);
  const open = useProgression((s) => s.openPanel);
  if (!loaded) return null;
  const level = person?.level ?? 1;
  const fraction = levelFraction(person);
  const petLevel = pet?.level ?? 1;
  const xpText = person && person.xpForNext > 0
    ? t("society.level.xp_of").replace("{0}", String(person.xpIntoLevel)).replace("{1}", String(person.xpForNext))
    : person ? t("society.level.max") : t("society.level.xp_of").replace("{0}", "0").replace("{1}", "40");
  return (
    <button type="button" className="office-card level-hud" data-compact={compact || undefined} onClick={() => open("you")}
      aria-label={t("society.level.open_panel")} aria-keyshortcuts="L">
      <LevelRing kind="person" level={level} fraction={fraction} size={compact ? 34 : 44} />
      <span className="level-hud-body">
        <span className="level-hud-name">
          <strong>{playerName}</strong>
          <em>{t(`society.level.title.${person?.title || "newcomer"}`)}</em>
        </span>
        <XpBar fraction={fraction} kind="person" label={t("society.level.xp_label")} />
        {!compact && <span className="level-hud-xp">{xpText}</span>}
        {!compact && (
          <span className="level-hud-pet">
            <span>{t("society.level.pet_line").replace("{0}", petName).replace("{1}", String(petLevel))}</span>
            <XpBar fraction={levelFraction(pet)} kind="pet" label={t("society.level.pet_xp_label").replace("{0}", petName)} />
          </span>
        )}
      </span>
    </button>
  );
}

/** An agent's level-up: a quiet card in the corner, gone on its own. */
export function LevelToasts({ names }: { names: ReadonlyMap<string, string> }) {
  const t = useT();
  const toasts = useProgression((s) => s.toasts);
  const dismiss = useProgression((s) => s.dismissToast);
  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((toast) => setTimeout(() => dismiss(toast.id), TOAST_MS));
    return () => timers.forEach(clearTimeout);
  }, [toasts, dismiss]);
  if (toasts.length === 0) return null;
  return (
    <div className="level-toasts" role="status" aria-live="polite" data-office-ui>
      {toasts.map((toast) => <LevelToast key={toast.id} toast={toast} name={names.get(toast.subjectId.slice("agent:".length)) ?? toast.subjectId.slice("agent:".length)}
        onClose={() => dismiss(toast.id)} label={t} />)}
    </div>
  );
}

function LevelToast({ toast, name, onClose, label }: { toast: Celebration; name: string; onClose: () => void; label: (key: string) => string }) {
  return (
    <button type="button" className="level-toast" onClick={onClose}>
      <LevelChip kind="agent" level={toast.level} />
      <span>
        <strong>{label("society.level.agent_up").replace("{0}", name).replace("{1}", String(toast.level))}</strong>
        <em>{toast.away ? label("society.level.while_away") : label(`society.level.title.${toast.title || "rookie"}`)}</em>
      </span>
    </button>
  );
}
