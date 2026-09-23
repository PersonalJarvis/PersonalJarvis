import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import { AppShell } from "./shared";

/**
 * Source-faithful, deterministic presentation of an illustrative session.
 * UI provenance: codex/ultra-agent-swarm @
 * 8434ce9b6e107c0b28789c144243e4b2b784723a (not yet an ancestor of main).
 * Source paths under jarvis/ui/web/frontend/src/:
 * views/swarm/UltraSwarmView.tsx; components/swarm/{CreateTeamForm,
 * SwarmPreparation,SwarmSimulation,SwarmInspector}.tsx; components/swarm/swarm.css;
 * i18n/locales/swarm/en.json; index.css (dark theme tokens).
 * The markup and class rules follow those components. Network calls and event
 * handlers are replaced with frame-driven illustrative inputs, never live work.
 * No successful task, accepted artifact, throughput, or measured usage is shown.
 */

const GOAL = "Compare three approaches for a local knowledge base.";
const ACCEPTANCE = "Compare setup, search, privacy, and maintenance. Cite sources and explain trade-offs.";
const PREPARATION_HINT = "First clarify the goal, then review the plan. Execution agents start only after you approve it. Questions and planning count toward this team's budget.";

type DemoTask = {
  id: string;
  title: string;
  description: string;
  acceptance: string;
  dependencies: string[];
  state: "ready" | "running";
};
type DemoAgent = {
  id: string;
  name: string;
  role: "lead" | "worker";
  state: "waiting" | "running";
  task_id: string | null;
  level: number;
};

const TASKS: DemoTask[] = [
  { id: "markdown", title: "Research Markdown files", description: "Assess plain files and full-text search.", acceptance: "Document setup, privacy, and maintenance.", dependencies: [], state: "running" },
  { id: "sqlite", title: "Research SQLite search", description: "Assess an embedded database with FTS5.", acceptance: "Document search capabilities and trade-offs.", dependencies: [], state: "running" },
  { id: "vectors", title: "Research local vectors", description: "Assess local embeddings and vector retrieval.", acceptance: "Document local resource requirements.", dependencies: [], state: "running" },
  { id: "comparison", title: "Compare the approaches", description: "Combine the findings into a sourced comparison.", acceptance: ACCEPTANCE, dependencies: ["markdown", "sqlite", "vectors"], state: "ready" },
];

const AGENTS: DemoAgent[] = [
  { id: "lead", name: "Lead", role: "lead", state: "waiting", task_id: null, level: 1 },
  { id: "worker-1", name: "Worker 1", role: "worker", state: "running", task_id: "markdown", level: 1 },
  { id: "worker-2", name: "Worker 2", role: "worker", state: "running", task_id: "sqlite", level: 1 },
  { id: "worker-3", name: "Worker 3", role: "worker", state: "running", task_id: "vectors", level: 1 },
];

// Source CSS with transitions removed: every motion must be seekable in Remotion.
const SOURCE_CSS = `
.swarm-root { height:100%; overflow:hidden; padding:24px; color:hsl(var(--foreground)); background:hsl(var(--background)); --swarm-accent:hsl(var(--primary)); --background:0 0% 4%; --card:0 0% 9%; --muted:0 0% 12%; --border:0 0% 15%; --foreground:0 0% 98%; --muted-foreground:0 0% 63%; --primary:0 0% 98%; --primary-foreground:0 0% 4%; --destructive:0 72% 55%; font-size:14px; }
.swarm-root *, .swarm-root *::before, .swarm-root *::after { box-sizing:border-box; }
.swarm-root h1,.swarm-root h2,.swarm-root h3,.swarm-root p,.swarm-root ol,.swarm-root ul,.swarm-root dl,.swarm-root dd { margin:0; }
.swarm-root h1 { font-size:26px; font-weight:650; letter-spacing:-.035em; }
.swarm-root h2 { font-size:18px; font-weight:600; }
.swarm-root h3 { font-size:14px; font-weight:600; }
.swarm-root p { line-height:1.6; }
.swarm-root summary { cursor:pointer; font-size:12px; margin:10px 0; }
.swarm-root a { font-size:12px; color:hsl(var(--primary)); text-decoration:underline; }
.swarm-root button,.swarm-root select,.swarm-root input,.swarm-root textarea { font-family:inherit; border:1px solid hsl(var(--border)); border-radius:8px; background:hsl(var(--background)); color:inherit; font-size:13px; padding:8px 11px; }
.swarm-root textarea { resize:none; line-height:1.5; }
.swarm-root button:disabled { opacity:.5; }
.swarm-root .swarm-primary { background:hsl(var(--primary)); color:hsl(var(--primary-foreground)); border-color:transparent; }
.swarm-root .swarm-danger { color:hsl(var(--destructive)); }
.swarm-row { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.swarm-actions { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.swarm-muted { color:hsl(var(--muted-foreground)); font-size:12px; }
.swarm-layout { display:grid; grid-template-columns:240px minmax(0,1fr); gap:20px; margin-top:24px; align-items:start; }
.swarm-sidebar { display:grid; gap:12px; }
.swarm-team-list { display:grid; gap:6px; }
.swarm-root .swarm-team { text-align:left; padding:13px; display:grid; gap:6px; }
.swarm-root .swarm-team[aria-current=true] { border-color:hsl(var(--primary)); background:hsl(var(--primary)/.06); }
.swarm-team strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.swarm-state { display:inline-flex; align-items:center; gap:6px; font-size:11px; font-weight:550; border:1px solid hsl(var(--border)); padding:3px 7px; border-radius:20px; width:fit-content; white-space:nowrap; }
.swarm-state::before { content:""; width:5px; height:5px; border-radius:50%; background:currentColor; }
.swarm-state[data-state=running] { color:hsl(var(--primary)); }
.swarm-main { min-width:0; display:grid; gap:16px; }
.swarm-panel { border:1px solid hsl(var(--border)); border-radius:12px; padding:18px; min-width:0; background:hsl(var(--card)); }
.swarm-goal { white-space:pre-wrap; overflow-wrap:anywhere; max-height:130px; overflow:auto; font-size:13px; margin:10px 0!important; }
.swarm-create { width:100%; max-width:760px; padding:22px; margin:20px auto; border:1px solid hsl(var(--border)); border-radius:12px; display:grid; gap:15px; background:hsl(var(--card)); }
.swarm-create label { display:grid; gap:6px; font-size:13px; }
.swarm-prompt-file { color:hsl(var(--muted-foreground)); }
.swarm-prompt-file input { max-width:100%; }
.swarm-preparation { display:grid; gap:14px; }
.swarm-preparation form,.swarm-question,.swarm-plan-review { display:grid; gap:12px; }
.swarm-question { border-top:1px solid hsl(var(--border)); padding-top:14px; }
.swarm-question label { font-size:14px; font-weight:600; }
.swarm-question textarea { width:100%; }
.swarm-question > button { justify-self:start; }
.swarm-preparation-steps { display:flex; flex-wrap:wrap; gap:10px 28px; list-style:decimal inside; color:hsl(var(--muted-foreground)); font-size:13px; padding:0; }
.swarm-preparation-steps [aria-current=step] { color:hsl(var(--primary)); font-weight:600; }
.swarm-plan-review ol,.swarm-plan-review ul { padding-left:22px; list-style:revert; }
.swarm-plan-review li { margin:8px 0; overflow-wrap:anywhere; }
.swarm-plan-text { white-space:pre-wrap; overflow-wrap:anywhere; }
.swarm-flat-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(155px,1fr)); gap:8px; padding:12px; max-height:400px; overflow:auto; }
.swarm-flat-grid button { display:grid; text-align:left; gap:5px; }
.swarm-flat-grid button[aria-pressed=true] { border-color:hsl(var(--primary)); background:hsl(var(--primary)/.05); }
.swarm-simulation { margin-top:12px; }
.swarm-simulation .swarm-flat-grid { padding:12px 0; }
.swarm-simulation button[data-state=running] { border-color:hsl(var(--primary)/.6); background:hsl(var(--primary)/.05); }
.swarm-task-flow { display:flex; gap:16px; overflow:hidden; padding:4px 0 12px; list-style:none; }
.swarm-task-flow li { flex:0 0 210px; }
.swarm-task-flow button { width:100%; height:100%; display:grid; gap:8px; text-align:left; overflow-wrap:anywhere; }
.swarm-tabs { display:flex; gap:5px; overflow:hidden; padding-bottom:6px; }
.swarm-tabs button { white-space:nowrap; }
.swarm-tabs button[aria-pressed=true] { border-color:hsl(var(--primary)); color:hsl(var(--primary)); }
.swarm-inspector { display:grid; grid-template-columns:minmax(180px,.8fr) minmax(240px,1.2fr); gap:14px; margin-top:14px; }
.swarm-record-list { display:grid; gap:6px; align-content:start; }
.swarm-root .swarm-record { display:grid; gap:5px; text-align:left; min-width:0; }
.swarm-record[aria-pressed=true] { border-color:hsl(var(--primary)); }
.swarm-detail { min-width:0; padding:14px; background:hsl(var(--muted)/.25); border-radius:10px; }
.swarm-detail dl { display:grid; gap:12px; margin-top:12px; }
.swarm-detail dt { color:hsl(var(--muted-foreground)); font-size:11px; }
.swarm-detail dd { font-size:12px; white-space:pre-wrap; overflow-wrap:anywhere; }
.swarm-pagination { display:flex; gap:10px; align-items:center; justify-content:flex-end; margin-top:12px; }
`;

const tween = (frame: number, from: number, to: number, a = 0, b = 1) =>
  interpolate(frame, [from, to], [a, b], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.22, 1, 0.36, 1),
  });

const State: React.FC<{ value: string }> = ({ value }) => (
  <span className="swarm-state" data-state={value}>{value[0].toUpperCase() + value.slice(1)}</span>
);

const GoalForm: React.FC<{ frame: number }> = ({ frame }) => {
  const text = GOAL.slice(0, Math.floor(tween(frame, 12, 54, 0, GOAL.length)));
  return <form className="swarm-create" aria-label="New team">
    <div className="swarm-row"><h2>New team</h2></div>
    <label>What should this team accomplish?<textarea name="goal" rows={6} value={text} readOnly placeholder="Describe the result you want. Jarvis will work out the steps." /></label>
    <label className="swarm-prompt-file">Choose or drop a prompt file (.txt or .md)<input type="file" accept=".txt,.md" /></label>
    <p className="swarm-muted">Jarvis asks a few focused questions and prepares a plan. You review it before the team starts working.</p>
    <details><summary>Options · budget and access</summary></details>
    <p className="swarm-muted">The swarm stops at its limits. Adjust them under Options.</p>
    <button className="swarm-primary" type="button" style={{ transform: `scale(${tween(frame, 72, 78, 1, 0.985) + tween(frame, 78, 84, 0, 0.015)})` }}>Clarify the goal</button>
  </form>;
};

const Preparation: React.FC<{ frame: number; plan: boolean }> = ({ frame, plan }) => {
  const answer = "A personal, offline-first library of Markdown notes.";
  return <section className="swarm-panel swarm-preparation" aria-label="Prepare your swarm">
    <div className="swarm-row"><h2>Prepare your swarm</h2><button>Refresh</button></div>
    <p className="swarm-muted">{PREPARATION_HINT}</p>
    <ol className="swarm-preparation-steps">
      <li aria-current={!plan ? "step" : undefined}>Clarify the goal</li>
      <li aria-current={plan ? "step" : undefined}>Review the plan</li>
      <li>Start swarm</li>
    </ol>
    {!plan ? <form aria-label="Clarify the goal">
      <div className="swarm-question">
        <label htmlFor="demo-swarm-answer">What kind of knowledge should the system organize?</label>
        <p className="swarm-muted">This helps make the comparison specific to your use case.</p>
        <textarea id="demo-swarm-answer" rows={2} readOnly value={answer.slice(0, Math.floor(tween(frame, 102, 137, 0, answer.length)))} />
        <button type="button">Let Jarvis propose this</button>
      </div>
      <div className="swarm-actions"><button className="swarm-primary" type="button">Create plan</button></div>
    </form> : <div className="swarm-plan-review">
      <h3>Review the plan</h3><p>Research three local approaches, then compare their trade-offs.</p>
      <h3>What should this team accomplish?</h3><p className="swarm-plan-text">{GOAL}</p>
      <h3>What must the final result demonstrate?</h3><p className="swarm-plan-text">{ACCEPTANCE}</p>
      <h3>Assumptions to review</h3><ul><li>A personal Markdown library; all stored notes remain local.</li></ul>
      <h3>Outside this plan</h3><ul><li>No software installation or changes to your files.</li></ul>
      <h3>First work steps</h3><ol>{TASKS.map(task => <li key={task.id}><strong>{task.title}</strong><p>{task.description}</p><p className="swarm-muted">{task.acceptance}</p></li>)}</ol>
      <details><summary>Options · budget and access</summary></details>
      <p className="swarm-muted">Starting approves this saved plan and its limits. No execution agents have started yet.</p>
      <div className="swarm-actions"><button>Change answers</button><button className="swarm-primary">Approve plan and start swarm</button></div>
    </div>}
  </section>;
};

/** Flat SwarmSimulation markup with the source wire fields preserved. */
const TeamSimulation: React.FC<{ frame: number }> = ({ frame }) => {
  const selected = frame >= 372 ? "sqlite" : "";
  const byId = new Map(TASKS.map(task => [task.id, task]));
  return <section className="swarm-panel" aria-label="Live teamwork">
    <div className="swarm-row"><h2>Live teamwork</h2><div className="swarm-actions"><button>Optional 3D view</button></div></div>
    <p className="swarm-muted">Agents: 4 · Tasks: 4 · Evidence &amp; artifacts: 0</p>
    <div className="swarm-actions"><button>Evidence &amp; artifacts: 0</button><button>Published results: 0</button></div>
    <div className="swarm-simulation">
      <p className="swarm-muted">The lead coordinates, workers execute, and evidence verifies the result. This view follows actual team activity.</p>
      <div className="swarm-flat-grid" role="group" aria-label="Agents">{AGENTS.map(agent => {
        const task = agent.task_id ? byId.get(agent.task_id) : undefined;
        return <button key={agent.id} aria-pressed={selected === agent.id} data-state={agent.state}>
          <strong>{agent.name}</strong><span>{agent.role === "lead" ? "Lead" : "Worker"} · {agent.state === "running" ? "Running" : "Waiting"}</span>
          {task && <span>{task.title}</span>}<span className="swarm-muted">Level {agent.level}</span>
        </button>;
      })}</div>
      <details open><summary>Task flow · current selection</summary>
        <ol className="swarm-task-flow">{TASKS.map(task => <li key={task.id}><button data-state={task.state} aria-pressed={selected === task.id}>
          <strong>{task.title}</strong><State value={task.state} />
          {task.dependencies.length > 0 && <span className="swarm-muted">Follows: {task.dependencies.map(id => byId.get(id)?.title ?? id).join(", ")}</span>}
        </button></li>)}</ol>
      </details>
    </div>
  </section>;
};

const Inspector: React.FC<{ selected: boolean }> = ({ selected }) => <section className="swarm-panel" aria-label="Inspect">
  <div className="swarm-tabs">{["Agents", "Tasks", "Collaboration", "Activity", "Decisions", "Checkpoint history", "Evidence & artifacts", "Verified reputation", "Published results"].map(tab => <button key={tab} aria-pressed={tab === "Tasks"}>{tab}</button>)}</div>
  <div className="swarm-row"><input placeholder="Find in this page" readOnly /><div className="swarm-actions"><button>All⌄</button><button>Refresh</button></div></div>
  <div className="swarm-inspector"><div className="swarm-record-list">{TASKS.map(task => <button key={task.id} className="swarm-record" aria-pressed={selected && task.id === "sqlite"}><strong>{task.title}</strong><State value={task.state} /></button>)}</div>
    <div className="swarm-detail">{selected ? <><h3>Research SQLite search</h3><dl>
      <div><dt>What should this team accomplish?</dt><dd>Assess an embedded database with FTS5.</dd></div>
      <div><dt>What must the final result demonstrate?</dt><dd>Document search capabilities and trade-offs.</dd></div>
      <div><dt>Filter by status</dt><dd>Running</dd></div>
      <div><dt>Owner</dt><dd><button>worker-2</button></dd></div>
      <div><dt>Evidence</dt><dd>[]</dd></div>
    </dl></> : <p className="swarm-muted">Inspect: Tasks</p>}</div>
  </div>
</section>;

/** 450 frames, 30 fps, 1600 × 900. Intended for a silent README loop. */
export const UltraSwarm: React.FC = () => {
  const frame = useCurrentFrame();
  const phase = frame < 90 ? "goal" : frame < 165 ? "clarify" : frame < 300 ? "plan" : "team";
  const start = phase === "goal" ? 0 : phase === "clarify" ? 90 : phase === "plan" ? 165 : 300;
  const scroll = phase === "plan"
    ? tween(frame, 204, 220, 0, 420) + tween(frame, 246, 260, 0, 300)
    : phase === "team"
      ? tween(frame, 300, 314, 0, 360) + tween(frame, 385, 399, 0, 480)
      : 0;
  const titles = { goal: "Start with your goal.", clarify: "Clarify what matters.", plan: "Review the plan. Approve the work.", team: "One lead. A coordinated team." };

  return <AppShell active="swarm" title={titles[phase]} subtitle="Ultra Agent Swarm">
    <style>{SOURCE_CSS}</style>
    <div className="swarm-root">
      <div style={{ transform: `translateY(${-scroll}px)`, position: "relative" }}>
        <header className="swarm-row"><div><h1>Ultra Agent Swarm</h1><p className="swarm-muted">Give Jarvis a goal. It plans the work and builds the team.</p></div><button className="swarm-primary">New team</button></header>
        <div className="swarm-layout">
          <aside className="swarm-sidebar" aria-label="Teams" style={{ transform: `translateY(${scroll}px)` }}>
            <div className="swarm-row"><h2>Teams</h2><button>Refresh</button></div><button style={{ textAlign: "left" }}>All <span style={{ float: "right" }}>⌄</span></button>
            <div className="swarm-team-list">{phase !== "goal" && <button className="swarm-team" aria-current="true"><strong>Local knowledge base</strong><State value={phase === "team" ? "running" : "created"} /></button>}</div>
            <div className="swarm-pagination"><button disabled>Previous</button><button disabled>Next</button></div>
          </aside>
          <div style={{ minWidth: 0, opacity: tween(frame, start, start + 8, 0.65, 1), transform: `translateY(${tween(frame, start, start + 12, 12, 0)}px)` }}>
            {phase === "goal" ? <GoalForm frame={frame} /> : <main className="swarm-main">
              <section className="swarm-panel">
                <div className="swarm-row"><h2>{phase === "team" ? "Local knowledge base" : "Your goal"}</h2>{phase === "team" && <span className="swarm-muted">Live</span>}</div>
                <div className="swarm-row"><State value={phase === "team" ? "running" : "created"} /><div className="swarm-actions">{phase === "team" && <><button>Pause</button><button className="swarm-danger">Stop</button></>}<button className="swarm-danger">Cancel</button><a>Open team separately</a></div></div>
                <p className="swarm-goal">{GOAL}</p>
                <details><summary>What must the final result demonstrate?</summary></details>
                <details><summary>Usage, limits and execution details</summary></details>
                <details><summary>Storage &amp; recovery</summary></details>
              </section>
              {phase !== "team" ? <Preparation frame={frame} plan={phase === "plan"} /> : <>
                <details className="swarm-panel"><summary>Specialist assignments</summary></details>
                <TeamSimulation frame={frame} />
                <Inspector selected={frame >= 372} />
              </>}
            </main>}
          </div>
        </div>
      </div>
    </div>
  </AppShell>;
};
