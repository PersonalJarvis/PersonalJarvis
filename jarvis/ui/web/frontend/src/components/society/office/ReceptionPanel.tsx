/**
 * Reception: the office's front desk and help centre. Three tabs — a welcome
 * with hiring and every place on the floor, the complete controls guide (from
 * officeControls, searchable), and the map's settings (officeSettings). H
 * opens it on the controls tab from anywhere, so nobody has to find the desk
 * to learn the keys.
 */
import { useId, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useT } from "@/i18n";
import type { OfficeLayout } from "./officeLayout";
import { useOfficeStore, type OfficeFloor } from "./officeStore";
import { CHECKPOINT_ICON, IconSvg } from "./CheckpointMarker";
import { arrowCap, CONTROL_GROUPS, controlsFor, mouseGesture, type ControlGroup, type OfficeControl } from "./officeControls";
import { useOfficeSettings, useReceptionTab, type ReceptionTab } from "./officeSettings";
import "./reception.css";

const TABS: readonly ReceptionTab[] = ["welcome", "controls", "settings"];

/** Stroke-only glyphs in a 24 × 24 box, drawn like the checkpoint icons. */
const GLYPH = {
  welcome: "M3 11l9-7 9 7M5.5 9.5V20h5v-6h3v6h5V9.5",
  controls: "M4 6h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zM7 10h.01M11 10h.01M15 10h.01M8 14h8",
  settings: "M4 7h9M17 7h3M15 5v4M4 17h3M11 17h9M9 15v4",
  move: "M12 3v18M3 12h18M9 6l3-3 3 3M18 9l3 3-3 3M9 18l3 3 3-3M6 9l-3 3 3 3",
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
  interact: "M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 10V8.5a1.5 1.5 0 0 1 3 0V12M15 11a1.5 1.5 0 0 1 3 0v3a7 7 0 0 1-7 7h-.5A6.5 6.5 0 0 1 5 16.5L3.8 13a1.5 1.5 0 0 1 2.7-1.2L9 15",
  map: "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14",
  arcade: "M7 9h10a4 4 0 0 1 0 8c-1.4 0-2.3-.8-2.8-2H9.8c-.5 1.2-1.4 2-2.8 2a4 4 0 0 1 0-8zM8 11.5v3M6.5 13h3M15.5 12.5h.01M17.5 14h.01",
  run: "M13 4a1.5 1.5 0 1 0 0 .01M9 20l3-6 3 2v4M7 12l3-4 4 1 2 3 3 1",
  bar: "M3 17h18v3H3zM6 18.5h.01M10 18.5h6M3 5h18v8H3z",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4",
  chevron: "M9 6l6 6-6 6",
  reset: "M4 12a8 8 0 1 0 2.4-5.7M4 4v5h5",
  ArrowUp: "M12 19V5M6 11l6-6 6 6",
  ArrowDown: "M12 5v14M6 13l6 6 6-6",
  ArrowLeft: "M19 12H5M11 6l-6 6 6 6",
  ArrowRight: "M5 12h14M13 6l6 6-6 6",
} as const;

type GlyphName = keyof typeof GLYPH;

function Glyph({ name }: { name: GlyphName }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={GLYPH[name]} />
    </svg>
  );
}

const GROUP_GLYPH: Record<ControlGroup, GlyphName> = { move: "move", camera: "camera", interact: "interact", map: "map", arcade: "arcade" };

/** How a key cap reads aloud and in the search ("Space", "Arrow up", "Drag"). */
function useCapLabel() {
  const t = useT();
  return (cap: string) => {
    const gesture = mouseGesture(cap);
    if (gesture) return t(`society.office.guide.mouse_${gesture}`);
    const arrow = arrowCap(cap);
    if (arrow) return t(`society.office.guide.key_${arrow}`);
    return cap === "Space" ? t("society.office.guide.key_space") : cap;
  };
}

function KeyCaps({ control }: { control: OfficeControl }) {
  const t = useT();
  const label = useCapLabel();
  return (
    <span className="office-rx-keys">
      {control.keys.map((alt, i) => (
        <span key={i} className="office-rx-alt">
          {i > 0 && <span className="office-rx-or">{t("society.office.guide.or")}</span>}
          {alt.map((cap, j) => {
            if (cap === "+") return <span key={j} className="office-rx-plus" aria-hidden>+</span>;
            if (mouseGesture(cap)) return <kbd key={j} className="office-rx-cap" data-mouse>{label(cap)}</kbd>;
            if (arrowCap(cap)) return <kbd key={j} className="office-rx-cap" aria-label={label(cap)}><Glyph name={cap as GlyphName} /></kbd>;
            return <kbd key={j} className="office-rx-cap">{label(cap)}</kbd>;
          })}
        </span>
      ))}
    </span>
  );
}

function ControlsTab({ floor }: { floor: OfficeFloor }) {
  const t = useT();
  const label = useCapLabel();
  const [query, setQuery] = useState("");
  const needle = query.trim().toLocaleLowerCase();
  // A search matches the action's words, or names one of its keys exactly ("e", "shift").
  const matches = (control: OfficeControl) => !needle
    || t(`society.office.guide.ctl_${control.id}`).toLocaleLowerCase().includes(needle)
    || control.keys.flat().some((cap) => cap !== "+" && label(cap).toLocaleLowerCase() === needle);
  const groups = CONTROL_GROUPS
    .map((group) => ({ group, controls: controlsFor(group, floor).filter(matches) }))
    .filter((g) => g.controls.length > 0);
  return (
    <>
      <label className="office-rx-search">
        <Glyph name="search" />
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("society.office.guide.search")}
          aria-label={t("society.office.guide.search")}
          onKeyDown={(e) => { if (e.key === "Escape" && query) { e.preventDefault(); e.stopPropagation(); setQuery(""); } }} />
      </label>
      {groups.length === 0 ? <p className="office-rx-empty" role="status">{t("society.office.guide.search_empty")}</p> : (
        <div className="office-rx-groups">
          {groups.map(({ group, controls }) => (
            <section key={group} className="office-rx-group" aria-label={t(`society.office.guide.group_${group}`)}>
              <h3><Glyph name={GROUP_GLYPH[group]} />{t(`society.office.guide.group_${group}`)}</h3>
              <ul>
                {controls.map((control) => (
                  <li key={control.id}>
                    <span className="office-rx-what">{t(`society.office.guide.ctl_${control.id}`)}</span>
                    <KeyCaps control={control} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

function Toggle({ glyph, label, hint, checked, onChange }: {
  glyph: GlyphName; label: string; hint: string; checked: boolean; onChange: (next: boolean) => void;
}) {
  const hintId = useId();
  return (
    <div className="office-rx-setting">
      <span className="office-rx-icon"><Glyph name={glyph} /></span>
      <div>
        <strong>{label}</strong>
        <span id={hintId}>{hint}</span>
      </div>
      <button type="button" role="switch" className="office-rx-switch" aria-checked={checked} aria-label={label}
        aria-describedby={hintId} onClick={() => onChange(!checked)}>
        <i aria-hidden />
      </button>
    </div>
  );
}

function SettingsTab() {
  const t = useT();
  const settings = useOfficeSettings();
  return (
    <>
      <p className="office-rx-lead">{t("society.office.guide.settings_intro")}</p>
      <h3 className="office-rx-heading">{t("society.office.guide.set_section_move")}</h3>
      <div className="office-rx-settings">
        <Toggle glyph="run" label={t("society.office.guide.set_always_run")} hint={t("society.office.guide.set_always_run_hint")}
          checked={settings.alwaysRun} onChange={(alwaysRun) => settings.set({ alwaysRun })} />
      </div>
      <h3 className="office-rx-heading">{t("society.office.guide.set_section_view")}</h3>
      <div className="office-rx-settings">
        <Toggle glyph="bar" label={t("society.office.guide.set_hint_bar")} hint={t("society.office.guide.set_hint_bar_hint")}
          checked={settings.showHintBar} onChange={(showHintBar) => settings.set({ showHintBar })} />
      </div>
      <button type="button" className="office-rx-reset" onClick={settings.reset}><Glyph name="reset" />{t("society.office.guide.set_reset")}</button>
    </>
  );
}

function WelcomeTab({ floor, layout, onCreateAgent }: { floor: OfficeFloor; layout: OfficeLayout; onCreateAgent?: () => void }) {
  const t = useT();
  const places = layout.checkpoints.filter((cp) => cp.id !== "create");
  const walk = (x: number, z: number) => {
    const store = useOfficeStore.getState();
    store.select(null);
    store.requestWalk({ x, z });
  };
  return (
    <>
      {floor === "agents" && (
        <div className="office-rx-hero">
          <span className="office-rx-icon" data-tone="gold"><IconSvg icon="plus" /></span>
          <div>
            <strong>{t("society.office.guide.hero_title")}</strong>
            <p>{t("society.office.create_body")}</p>
            <button type="button" className="office-action office-action-primary" disabled={!onCreateAgent} onClick={() => onCreateAgent?.()}>
              {t("society.office.create_action")}
            </button>
          </div>
        </div>
      )}
      <h3 className="office-rx-heading">{t("society.office.guide.places")}</h3>
      <ul className="office-rx-places">
        {places.map((cp) => {
          const name = t(`society.office.cp_${cp.id}`);
          return (
            <li key={cp.id}>
              <button type="button" className="office-rx-place" aria-label={t("society.office.guide.place_walk_label").replace("{0}", name)}
                onClick={() => walk((cp.approach ?? cp).x, (cp.approach ?? cp).z)}>
                <span className="office-rx-icon"><IconSvg icon={CHECKPOINT_ICON[cp.id]} /></span>
                <span className="office-rx-place-text">
                  <strong>{name}</strong>
                  <span>{t(`society.office.cp_${cp.id}_hint`)}</span>
                </span>
                <span className="office-rx-go" aria-hidden>{t("society.office.guide.place_walk")}<Glyph name="chevron" /></span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="office-rx-tip"><kbd className="office-rx-cap">H</kbd>{t("society.office.guide.tip_guide")}</p>
    </>
  );
}

export function ReceptionPanel({ floor, layout, onCreateAgent }: { floor: OfficeFloor; layout: OfficeLayout; onCreateAgent?: () => void }) {
  const t = useT();
  const tab = useReceptionTab((s) => s.tab);
  const setTab = useReceptionTab((s) => s.setTab);
  const baseId = useId();
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  // Arrow keys move between tabs (WAI-ARIA tabs pattern); the map's walking keys listen on the window, not here.
  const onTabKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    event.stopPropagation();
    const next = TABS[(TABS.indexOf(tab) + step + TABS.length) % TABS.length]!;
    setTab(next);
    tabRefs.current[TABS.indexOf(next)]?.focus();
  };
  return (
    <div className="office-reception">
      <div className="office-rx-tabs" role="tablist" aria-label={t("society.office.guide.tablist")} onKeyDown={onTabKey}
        style={{ "--rx-tab": TABS.indexOf(tab) } as CSSProperties}>
        <i className="office-rx-tab-thumb" aria-hidden />
        {TABS.map((id, i) => (
          <button key={id} ref={(el) => { tabRefs.current[i] = el; }} type="button" role="tab" id={`${baseId}-${id}`}
            aria-selected={tab === id} aria-controls={`${baseId}-${id}-panel`} tabIndex={tab === id ? 0 : -1}
            className="office-rx-tab" onClick={() => setTab(id)}>
            <Glyph name={id} />{t(`society.office.guide.tab_${id}`)}
          </button>
        ))}
      </div>
      {/* Keyed by tab so each switch replays the short entrance. */}
      <div key={tab} role="tabpanel" id={`${baseId}-${tab}-panel`} aria-labelledby={`${baseId}-${tab}`} className="office-rx-body">
        {tab === "welcome" && <WelcomeTab floor={floor} layout={layout} onCreateAgent={onCreateAgent} />}
        {tab === "controls" && <ControlsTab floor={floor} />}
        {tab === "settings" && <SettingsTab />}
      </div>
    </div>
  );
}
