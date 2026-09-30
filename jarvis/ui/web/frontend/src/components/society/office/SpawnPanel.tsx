/**
 * The spawn point's panel. On the agents floor it spawns a new Jarvis agent
 * through the app's create dialog; on the coding floor it starts new coding
 * agents in the Agentic IDE with Mission Control's launcher. Whoever is new
 * then appears on the spawn pad and walks to a free desk. Like every office
 * panel it uses existing app paths only.
 */
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { IconSvg } from "./CheckpointMarker";
import { StartAgent } from "./MissionControlPanel";
import "./spawnPanel.css";

const STEPS = ["job", "brain", "arrive"] as const;

function Hero({ title, body }: { title: string; body: string }) {
  return (
    <div className="office-spawn-hero">
      <span className="office-spawn-mark" aria-hidden><IconSvg icon="spawn" /></span>
      <div>
        <strong>{title}</strong>
        <p>{body}</p>
      </div>
    </div>
  );
}

/** Agents floor: what a new Jarvis agent needs, and the button that opens the create dialog. */
export function SpawnPanel({ agents, onCreateAgent }: { agents: readonly SocietyAgent[]; onCreateAgent?: () => void }) {
  const t = useT();
  const working = agents.filter((a) => a.state === "working").length;
  return (
    <div className="office-spawn">
      <Hero title={t("society.office.spawn_title")} body={t("society.office.spawn_body")} />
      <ol className="office-spawn-steps">
        {STEPS.map((step, i) => (
          <li key={step}><span aria-hidden>{i + 1}</span>{t(`society.office.spawn_step_${step}`)}</li>
        ))}
      </ol>
      <button type="button" className="office-spawn-go" disabled={!onCreateAgent} onClick={() => onCreateAgent?.()}>
        <IconSvg icon="plus" />{t("society.office.spawn_action")}
      </button>
      <p className="office-spawn-foot" role="status">
        {t("society.office.spawn_team").replace("{0}", String(agents.length)).replace("{1}", String(working))}
      </p>
    </div>
  );
}

/** Coding floor: Mission Control's launcher, right at the spawn point. */
export function LaunchPanel() {
  const t = useT();
  return (
    <div className="office-spawn">
      <Hero title={t("society.office.launch_title")} body={t("society.office.launch_body")} />
      <div className="office-mc"><StartAgent /></div>
    </div>
  );
}
