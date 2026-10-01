import { useSwarmText } from "./strings";
import { exactCount, type RecordKind, type WorldSnapshot } from "./types";
import { worldNodes } from "./worldLayout";

/** A bounded projection of real work; no timer fabricates progress or messages. */
export function SwarmSimulation({ snapshot, selected, onAgent, onRecord }: {
  snapshot: WorldSnapshot;
  selected: string;
  onAgent: (id: string, group?: string) => void;
  onRecord: (kind: RecordKind, id: string) => void;
}) {
  const t = useSwarmText();
  const tasks = new Map(snapshot.tasks.map(task => [task.id, task]));
  return <div className="swarm-simulation">
    <p className="swarm-muted">{t("simulationHint")}</p>
    <div className="swarm-flat-grid" role="group" aria-label={t("agents")}>
      {worldNodes(snapshot).map(node => {
        const agent = node.agent;
        const task = agent?.task_id ? tasks.get(agent.task_id) : undefined;
        return <button key={node.id} aria-pressed={selected === node.id} data-state={agent?.state}
          onClick={() => onAgent(agent?.id ?? node.id, node.group)}>
          <strong>{node.title}</strong>
          <span>{node.group ? `${exactCount(node.count ?? "0")} ${t("agents")}` : `${t(agent?.role ?? "worker")} · ${t(agent?.state ?? "idle")}`}</span>
          {task && <span>{task.title}</span>}
          {agent?.tool_activity && <span className="swarm-muted">{t("toolActivity")}: {agent.tool_activity}</span>}
          <span className="swarm-muted">{t("level")} {node.level}</span>
        </button>;
      })}
    </div>
    {snapshot.tasks.length > 0 && <details open>
      <summary>{t("taskFlow")}</summary>
      <ol className="swarm-task-flow">
        {snapshot.tasks.slice(0, 20).map(task => <li key={task.id}>
          <button data-state={task.state} aria-pressed={selected === task.id} onClick={() => onRecord("tasks", task.id)}>
            <strong>{task.title}</strong><span className="swarm-state" data-state={task.state}>{t(task.state)}</span>
            {task.dependencies.length > 0 && <span className="swarm-muted">{t("follows")}: {task.dependencies.map(id => tasks.get(id)?.title ?? id).join(", ")}</span>}
          </button>
        </li>)}
      </ol>
    </details>}
    {snapshot.activity.length > 0 && <div className="swarm-recent-work" role="group" aria-label={t("recentWork")}>
      <h3>{t("recentWork")}</h3>
      {snapshot.activity.slice(0, 6).map(event => <button key={event.id} onClick={() => onRecord("events", event.id)}>
        <span>{event.summary}</span><span className="swarm-muted">#{exactCount(event.seq)}</span>
      </button>)}
    </div>}
  </div>;
}
