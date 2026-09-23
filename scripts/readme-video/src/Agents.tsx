import React from "react";
import { Easing, interpolate } from "remotion";
import {
  ArrowLeft, ChevronDown, Clock, Maximize2, Mic, MoreHorizontal,
  MousePointer2, Plus, Search, Send,
} from "lucide-react";
import { Badge } from "@app/components/ui/badge";
import { Button } from "@app/components/ui/button";
import { Switch } from "@app/components/ui/switch";
import { GigiMark } from "@app/components/GigiMark";
import { AppShell, useDemoFrame } from "./shared";

/**
 * Deterministic visual adapter for the actual Agents workspace, not a second UI.
 * Layout/classes: society/card/AgentCardOverlay.tsx, roster/RosterRail.tsx,
 * chat/AgentChatPanel.tsx, card/OptionsRail.tsx, card/AgentRoutineDetail.tsx.
 * The live components own sockets, stores and mutation hooks; this adapter
 * keeps their rendered structure and supplies explicitly illustrative data.
 * Badge, Button and Switch are the production components. No API runs here.
 */
const AGENTS = [
  { name: "Jarvis", title: "Lead", primary: "#343338", secondary: "#737078", accent: "#d4b477" },
  { name: "Atlas", title: "Planning & research", primary: "#1f2a44", secondary: "#f2f2ee", accent: "#c9a227" },
  { name: "Nova", title: "Writing & review", primary: "#096153", secondary: "#b16f51", accent: "#e8c46b" },
] as const;
type DemoAgent = (typeof AGENTS)[number];

const EASE = Easing.bezier(0.16, 1, 0.3, 1);
const move = (frame: number, start: number, end: number, from: number, to: number) =>
  interpolate(frame, [start, end], [from, to], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE,
  });
const glide = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, end], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic),
  });
const typed = (text: string, frame: number, start: number, duration: number) =>
  text.slice(0, Math.floor(interpolate(frame, [start, start + duration], [0, text.length], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  })));

/** Exact figure-less fallback from society/AgentSwatch.tsx. */
const Face: React.FC<{ agent: DemoAgent; size: number }> = ({ agent, size }) => (
  <span className="relative inline-flex shrink-0 items-end justify-center overflow-hidden rounded-full border border-border"
    style={{ width: size, height: size, background: `linear-gradient(170deg, ${agent.primary} 0%, ${agent.secondary} 135%)`, boxShadow: `inset 0 0 0 2px ${agent.accent}66` }}>
    <span className="relative rounded-full" style={{ width: size * 0.64, height: size * 0.64, marginBottom: size * 0.08, background: "#f4b68f", boxShadow: `0 ${-size * 0.16}px 0 0 #9e5d47, inset 0 0 0 ${Math.max(1, size * 0.03)}px rgba(0,0,0,0.08)` }}>
      <span className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2" style={{ gap: Math.max(3, size * 0.11), marginTop: size * 0.04 }}>
        {[0, 1].map((eye) => <span key={eye} className="rounded-full" style={{ width: Math.max(2.5, size * 0.085), height: Math.max(2.5, size * 0.085) * 1.25, background: "#1b2427" }} />)}
      </span>
    </span>
  </span>
);

const Roster: React.FC<{ selected: string }> = ({ selected }) => (
  <aside className="flex h-full min-h-0 w-full flex-col border-0 border-border bg-sidebar jarvis-nav-surface">
    <div className="flex items-center justify-between gap-2 px-3 pt-3">
      <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">Agents</h2>
      <Button size="sm" variant="secondary" className="h-8 gap-1 px-2.5" style={{ transition: "none" }}><Plus className="h-3.5 w-3.5" />New</Button>
    </div>
    <label className="relative mx-3 mt-3 block">
      <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <input readOnly value="" placeholder="Search agents" className="h-8 w-full rounded-md border border-border bg-background pl-8 pr-2 text-sm text-foreground placeholder:text-muted-foreground" />
    </label>
    <div className="mt-2 min-h-0 flex-1">
      <div className="flex justify-center px-2 pb-2">
        <button className="flex flex-col items-center gap-1.5 rounded-xl bg-secondary/50 px-5 py-3 text-center">
          <GigiMark size={56} />
          <span className="flex items-center justify-center gap-1.5"><span className="text-sm font-medium text-foreground">Jarvis</span><Badge variant="secondary" className="px-1.5 py-0 text-xs">Lead</Badge></span>
        </button>
      </div>
      <div className="mx-3 mb-1 border-t border-border/60" />
      <ul className="flex flex-col gap-0.5 px-2 pb-3">
        {AGENTS.slice(1).map((agent) => <li key={agent.name}>
          <button className={`flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left ${selected === agent.name ? "bg-secondary" : ""}`}>
            <Face agent={agent} size={34} />
            <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-foreground">{agent.name}</span><span className="block truncate text-xs text-muted-foreground">{agent.title}</span></span>
            <span className="h-2 w-2 shrink-0 rounded-full bg-muted-foreground/50" />
          </button>
        </li>)}
      </ul>
    </div>
  </aside>
);

const BRIEF = "Plan the next release. Ask before changing any files.";
const UserMessage: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) =>
  <div className="flex min-w-0 max-w-[min(85%,42rem)] flex-col items-end gap-1 self-end" style={style}>
    <div className="min-w-0 rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-sm leading-relaxed text-foreground">{children}</div>
  </div>;
/** WorkTrace.tsx conversation answer shape; illustrative planning prose only. */
const PlanMessage: React.FC<{ children: React.ReactNode }> = ({ children }) =>
  <div className="w-full max-w-xl self-start rounded-2xl rounded-bl-md bg-secondary px-4 py-2.5 text-sm leading-relaxed text-foreground">{children}</div>;

const Chat: React.FC<{ frame: number; agent: DemoAgent }> = ({ frame, agent }) => {
  const draft = typed(BRIEF, frame, 52, 46);
  const submitted = frame >= 112;
  return <section className="flex h-full min-h-0 flex-col bg-background">
    <div className="min-h-0 flex-1 overflow-hidden px-6 py-5">
      <div className="mx-auto flex w-full min-w-0 flex-col gap-3" style={{ transform: `translateY(${-move(frame, 112, 134, 0, 34)}px)` }}>
        <p className="my-4 text-center text-[11px] text-muted-foreground">Today 09:00</p>
        <UserMessage>Help me organise the next release.</UserMessage>
        <PlanMessage>
          <p className="mb-2">We can plan it in four steps:</p>
          <ol className="list-decimal space-y-1 pl-5">
            <li>Define the outcome and what stays out of scope.</li>
            <li>Split the work into small, reviewable tasks.</li>
            <li>Decide who should review each result.</li>
            <li>Confirm the plan before execution.</li>
          </ol>
        </PlanMessage>
        <UserMessage>Use Atlas for planning and Nova for writing. Ask before making changes.</UserMessage>
        <PlanMessage>
          <p className="mb-2">A useful brief includes the goal, constraints and review step.</p>
          <p>For recurring work, an agent&apos;s routine has its own instruction and schedule. You can review and edit both from this workspace.</p>
        </PlanMessage>
        <p className="my-4 text-center text-[11px] text-muted-foreground">Today 09:02</p>
        {submitted && <UserMessage style={{ opacity: move(frame, 112, 120, 0, 1), transform: `translateY(${move(frame, 112, 130, 16, 0)}px)` }}>{BRIEF}</UserMessage>}
      </div>
    </div>
    <div className="shrink-0 px-6 pb-4 pt-2">
      <div className="relative mx-auto flex w-full min-w-0 items-end gap-1 rounded-[24px] border border-border bg-secondary px-2 py-1.5">
        <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground"><Plus className="h-4 w-4" /></button>
        <div className="min-h-8 min-w-0 flex-1 whitespace-pre-wrap px-1 py-1.5 text-sm leading-5 text-foreground">{!submitted && draft ? <>{draft}<span style={{ opacity: frame % 30 < 18 ? 1 : 0 }}>|</span></> : <span className="text-muted-foreground">Message {agent.name}</span>}</div>
        <button className="flex h-8 min-w-0 shrink-0 items-center gap-1.5 rounded-full px-2 py-1 text-xs text-muted-foreground"><span className="truncate">Jarvis&apos; brain</span><ChevronDown className="h-3 w-3 shrink-0" /></button>
        <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground"><Mic className="h-4 w-4" /></button>
        <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground" style={{ opacity: !submitted && draft ? 1 : 0.4 }}><Send className="h-3.5 w-3.5" /></button>
      </div>
    </div>
  </section>;
};

const routineField = "w-full rounded-lg border border-border bg-background px-3 py-2 text-[12px] text-foreground";
const routineButton = "rounded-md bg-secondary px-2.5 py-1.5 text-[12px] text-foreground";
const INSTRUCTION = "Review open issues and draft a short plan. Ask before changing files.";

/** Closed Combobox trigger, exactly as rendered by ui/combobox.tsx. */
const SelectField: React.FC<{ value: string }> = ({ value }) => <button className="flex w-full items-center gap-2 rounded-md bg-input px-3 py-2 text-left text-sm text-foreground">
  <span className="min-w-0 flex-1 truncate">{value}</span><ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
</button>;

const RoutineDetail: React.FC<{ frame: number }> = ({ frame }) => {
  const editing = frame >= 272 && frame < 375;
  const changing = frame >= 198 && frame < 254;
  const instruction = changing ? typed(INSTRUCTION, frame, 198, 40) : INSTRUCTION;
  // A translated scroll interior is frame-deterministic even when the renderer
  // seeks directly to a frame before browser layout effects have settled.
  // The original detail's order, dimensions and viewport clipping stay intact.
  const scroll = 255 * glide(frame, 262, 292) - 255 * glide(frame, 365, 393);
  return <section className="min-h-0 flex-1 overflow-hidden text-foreground" style={{ flexBasis: 0 }}>
    <div className="space-y-4 pb-4" style={{ transform: `translateY(${-scroll}px)` }}>
    <button className="flex items-center gap-1 text-[12px] text-muted-foreground"><ArrowLeft size={14} />Routines</button>
    <div className="flex flex-wrap items-center gap-2">
      <label className="mr-auto flex items-center gap-2 text-[12px]"><Switch checked onCheckedChange={() => undefined} style={{ transition: "none" }} />Active</label>
      <button className={routineButton}>Delete</button><button className={routineButton}>Test run</button>
    </div>
    <label className="block space-y-1 text-[11px] text-muted-foreground">Name<input className={routineField} readOnly value="Project review" /></label>
    <label className="block space-y-1 text-[11px] text-muted-foreground">Instruction<textarea className={routineField} readOnly rows={7} value={instruction} style={{ resize: "none" }} /></label>
    {changing && <div className="flex gap-2"><button className={routineButton}>Save</button><button className={routineButton}>Cancel</button></div>}
    <div className="space-y-2 rounded-xl border border-border p-3">
      <div className="flex items-center justify-between gap-2"><h4 className="text-[11px] text-muted-foreground">Model</h4><button className="text-[11px] text-muted-foreground underline">Change</button></div>
      <p className="text-[12px]">Follow agent</p>
    </div>
    <div className="space-y-2">
      <h4 className="text-[11px] text-muted-foreground">When to run</h4>
      <div className="space-y-2 rounded-xl border border-border p-3">
        <button className="flex w-full items-start gap-2 rounded text-left text-[12px]"><Clock size={14} className="mt-0.5 shrink-0" /><span>Every day at 09:00 (UTC)</span></button>
        <button className="flex items-center gap-1 text-[12px] text-muted-foreground"><Plus size={14} />Add another schedule</button>
      </div>
      {editing && <div className="space-y-2 rounded-lg border border-border p-3" style={{ opacity: move(frame, 272, 284, 0, 1) * (1 - move(frame, 365, 375, 0, 1)) }}>
        <SelectField value="Calendar schedule" />
        <label className="block text-[11px] text-muted-foreground">Time<input className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground" readOnly value={frame < 305 ? "09:00" : "10:00"} /></label>
        <label className="block text-[11px] text-muted-foreground">Timezone<input className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground" readOnly value="UTC" /></label>
        <button className={routineButton}>Save</button>{" "}<button className={routineButton}>Cancel</button>
      </div>}
    </div>
    {!editing && <div className="space-y-2"><h4 className="text-[11px] text-muted-foreground">Execution history</h4><p className="text-[12px] text-muted-foreground">No executions yet.</p></div>}
    </div>
  </section>;
};

const Options: React.FC<{ frame: number; name: string }> = ({ frame, name }) => {
  const detail = glide(frame, 156, 172) * (1 - glide(frame, 391, 407));
  return <aside className="flex h-full min-h-0 flex-col border-l border-border bg-sidebar">
  <header className="flex shrink-0 items-center justify-between gap-2 px-3 pb-1 pt-3">
    <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">Options</h2><button className="rounded-md p-1 text-muted-foreground"><MoreHorizontal className="h-4 w-4" /></button>
  </header>
  <div className="relative min-h-0 flex-1 overflow-hidden">
    <div className="absolute inset-0 flex min-h-0 flex-col gap-3 px-3 pb-3 pt-2" style={{ opacity: 1 - detail }}>
      <div className="shrink-0">
        <div className="mb-1 flex items-center justify-between gap-1 text-[10px] text-muted-foreground"><span>{name} · Connecting</span><button className="rounded px-2 py-1 text-xs"><Maximize2 size={14} /></button></div>
        <div className="relative flex aspect-[16/10] items-center justify-center overflow-hidden rounded-lg bg-muted p-3 text-center text-xs text-muted-foreground">Connecting</div>
        <div className="mt-2 flex justify-center"><button disabled className="rounded px-2 py-1 text-xs opacity-40">Take control / sign in</button></div>
      </div>
      <section className="flex min-h-0 flex-1 flex-col">
        <div className="mb-1.5 flex items-center justify-between gap-2"><h3 className="font-display text-[13px] font-semibold text-foreground">Routines<span className="ml-1.5 text-muted-foreground">1</span></h3><button className="rounded p-1 text-muted-foreground"><Plus size={14} /></button></div>
        <button className="flex w-full items-start gap-2 rounded-md px-0.5 py-1.5 text-left">
          <Clock size={16} className="mt-0.5 shrink-0 text-success" /><span className="min-w-0 flex-1"><span className="block text-[13px] font-medium text-foreground">Project review</span><span className="block text-[11px] leading-snug text-muted-foreground">Every day at 09:00 (UTC)</span></span>
        </button>
      </section>
    </div>
    <div className="absolute inset-0 flex min-h-0 flex-col px-3 pb-3 pt-2" style={{ opacity: detail, transform: `translateX(${12 * (1 - detail)}px)` }}>
      <RoutineDetail frame={frame} />
    </div>
  </div>
</aside>;
};

const Workspace: React.FC<{ frame: number }> = ({ frame }) => {
  const agent = frame < 40 ? AGENTS[2] : AGENTS[1];
  const focus = glide(frame, 148, 180) - glide(frame, 385, 420);
  const scale = 1 + focus * 0.4;
  const cursorVisible = (frame >= 8 && frame < 48) || (frame >= 130 && frame < 174);
  const cursorX = frame < 48 ? move(frame, 8, 36, 20, 6) : move(frame, 130, 156, 84, 93);
  const cursorY = frame < 48 ? move(frame, 8, 36, 39, 24) : move(frame, 130, 156, 96, 30.5);
  const click = frame < 48 ? Math.abs(frame - 40) < 4 : Math.abs(frame - 158) < 4;
  return <div className="relative grid h-full min-h-0 w-full" style={{ gridTemplateColumns: "12% minmax(0, 1fr)", gridTemplateRows: "minmax(0, 1fr)", transform: `scale(${scale})`, transformOrigin: "100% 0%" }}>
        <Roster selected={agent.name} />
        <div className="grid min-h-0 overflow-hidden rounded-tl-[12px] border-l border-border bg-background" style={{ gridTemplateColumns: "minmax(0, 1fr) 14.77%", gridTemplateRows: "minmax(0, 1fr)" }}>
          <Chat frame={frame} agent={agent} /><Options frame={frame} name={agent.name} />
        </div>
        {cursorVisible && <MousePointer2 fill="hsl(var(--foreground))" stroke="hsl(var(--background))" strokeWidth={1.7} size={24} style={{ position: "absolute", left: `${cursorX}%`, top: `${cursorY}%`, transform: `scale(${click ? 0.8 : 1})`, filter: "drop-shadow(0 2px 3px rgba(0,0,0,.4))" }} />}
      </div>;
};

export const Agents: React.FC = () => {
  const frame = useDemoFrame();
  const loop = glide(frame, 419, 446);
  return <AppShell active="agents">
    <div className="relative h-full min-h-0 flex-1 overflow-hidden bg-card" data-readme-agents>
      <style>{`[data-readme-agents] *, [data-readme-agents] *::before, [data-readme-agents] *::after { transition: none !important; animation: none !important; }`}</style>
      <div className="absolute inset-0"><Workspace frame={loop >= 1 ? 0 : frame} /></div>
      {loop > 0 && loop < 1 && <div className="absolute inset-0" style={{ opacity: loop }}><Workspace frame={0} /></div>}
    </div>
  </AppShell>;
};
