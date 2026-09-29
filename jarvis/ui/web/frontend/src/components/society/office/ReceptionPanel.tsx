/**
 * Reception: the office's front desk and help centre. Three tabs — a welcome
 * with hiring and every place on the floor, the complete controls guide (from
 * officeControls), and the map's settings (officeSettings). H opens it on the
 * controls tab from anywhere, so nobody has to find the desk to learn the keys.
 */
import { useId, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { useT } from "@/i18n";
import type { OfficeLayout } from "./officeLayout";
import { useOfficeStore, type OfficeFloor } from "./officeStore";
import { CHECKPOINT_ICON, IconSvg } from "./CheckpointMarker";
import { CONTROL_GROUPS, controlsFor, mouseGesture, type OfficeControl } from "./officeControls";
import { useOfficeSettings, useReceptionTab, type ReceptionTab } from "./officeSettings";
import "./reception.css";

const TABS: readonly ReceptionTab[] = ["welcome", "controls", "settings"];

function KeyCaps({ control }: { control: OfficeControl }) {
  const t = useT();
  return (
    <span className="office-rx-keys">
      {control.keys.map((alt, i) => (
        <span key={i} className="office-rx-alt">
          {i > 0 && <span className="office-rx-or">{t("society.office.guide.or")}</span>}
          {alt.map((cap, j) => {
            if (cap === "+") return <span key={j} className="office-rx-plus" aria-hidden>+</span>;
            const gesture = mouseGesture(cap);
            return gesture
              ? <kbd key={j} className="office-rx-cap" data-mouse>{t(`society.office.guide.mouse_${gesture}`)}</kbd>
              : <kbd key={j} className="office-rx-cap">{cap === "Space" ? t("society.office.guide.key_space") : cap}</kbd>;
          })}
        </span>
      ))}
    </span>
  );
}

function ControlsTab({ floor }: { floor: OfficeFloor }) {
  const t = useT();
  return (
    <div className="office-rx-groups">
      {CONTROL_GROUPS.map((group) => (
        <section key={group} className="office-rx-group" aria-label={t(`society.office.guide.group_${group}`)}>
          <h3>{t(`society.office.guide.group_${group}`)}</h3>
          <ul>
            {controlsFor(group, floor).map((control) => (
              <li key={control.id}>
                <span className="office-rx-what">{t(`society.office.guide.ctl_${control.id}`)}</span>
                <KeyCaps control={control} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (next: boolean) => void }) {
  const hintId = useId();
  return (
    <div className="office-rx-setting">
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
      <p className="office-hint">{t("society.office.guide.settings_intro")}</p>
      <div className="office-rx-settings">
        <Toggle label={t("society.office.guide.set_always_run")} hint={t("society.office.guide.set_always_run_hint")}
          checked={settings.alwaysRun} onChange={(alwaysRun) => settings.set({ alwaysRun })} />
        <Toggle label={t("society.office.guide.set_hint_bar")} hint={t("society.office.guide.set_hint_bar_hint")}
          checked={settings.showHintBar} onChange={(showHintBar) => settings.set({ showHintBar })} />
      </div>
      <button type="button" className="office-link office-rx-reset" onClick={settings.reset}>{t("society.office.guide.set_reset")}</button>
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
              <span className="office-rx-icon"><IconSvg icon={CHECKPOINT_ICON[cp.id]} /></span>
              <span className="office-rx-place">
                <strong>{name}</strong>
                <span>{t(`society.office.cp_${cp.id}_hint`)}</span>
              </span>
              <button type="button" className="office-mini" aria-label={t("society.office.guide.place_walk_label").replace("{0}", name)}
                onClick={() => walk(cp.x, cp.z)}>{t("society.office.guide.place_walk")}</button>
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
      <div className="office-rx-tabs" role="tablist" aria-label={t("society.office.guide.tablist")} onKeyDown={onTabKey}>
        {TABS.map((id, i) => (
          <button key={id} ref={(el) => { tabRefs.current[i] = el; }} type="button" role="tab" id={`${baseId}-${id}`}
            aria-selected={tab === id} aria-controls={`${baseId}-${id}-panel`} tabIndex={tab === id ? 0 : -1}
            className="office-rx-tab" onClick={() => setTab(id)}>
            {t(`society.office.guide.tab_${id}`)}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${baseId}-${tab}-panel`} aria-labelledby={`${baseId}-${tab}`} className="office-rx-body">
        {tab === "welcome" && <WelcomeTab floor={floor} layout={layout} onCreateAgent={onCreateAgent} />}
        {tab === "controls" && <ControlsTab floor={floor} />}
        {tab === "settings" && <SettingsTab />}
      </div>
    </div>
  );
}
