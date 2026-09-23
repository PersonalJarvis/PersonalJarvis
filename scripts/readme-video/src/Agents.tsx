import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import {
  ArrowLeft, ChevronDown, Clock, Maximize2, Mic, MoreHorizontal,
  MousePointer2, Plus, Search, Send,
} from "lucide-react";
import { Badge } from "@app/components/ui/badge";
import { Button } from "@app/components/ui/button";
import { Switch } from "@app/components/ui/switch";
import { AppShell } from "./shared";

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
          <Face agent={AGENTS[0]} size={56} />
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
const Chat: React.FC<{ frame: number; agent: DemoAgent }> = ({ frame, agent }) => {
  const draft = typed(BRIEF, frame, 70, 54);
  const submitted = frame >= 138;
  return <section className="flex h-full min-h-0 flex-col bg-background">
    {submitted ? <div className="min-h-0 flex-1 overflow-hidden px-6 py-5">
      <div className="mx-auto flex w-full min-w-0 flex-col gap-3">
        <p className="my-4 text-center text-[11px] text-muted-foreground">Today 09:00</p>
        <div className="flex min-w-0 max-w-[min(85%,42rem)] flex-col items-end gap-1 self-end" style={{ opacity: move(frame, 138, 145, 0, 1), transform: `translateY(${move(frame, 138, 150, 12, 0)}px)` }}>
          <div className="min-w-0 rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-sm leading-relaxed text-foreground">{BRIEF}</div>
        </div>
      </div>
    </div> : <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
      <Face agent={agent} size={56} />
      <p className="text-sm font-medium text-foreground">Talk to {agent.name}</p>
      <p className="max-w-[32ch] text-xs text-muted-foreground">Type, speak, or tag an agent, plugin or tool with @ — e.g. @gmail.</p>
    </div>}
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
  const editing = frame >= 285 && frame < 410;
  const changing = frame >= 230 && frame < 285;
  const instruction = changing ? typed(INSTRUCTION, frame, 230, 40) : INSTRUCTION;
  // A translated scroll interior is frame-deterministic even when the renderer
  // seeks directly to a frame before browser layout effects have settled.
  // The original detail's order, dimensions and viewport clipping stay intact.
  const scroll = move(frame, 285, 299, 0, 330) - move(frame, 410, 428, 0, 210);
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
      {editing && <div className="space-y-2 rounded-lg border border-border p-3" style={{ opacity: move(frame, 285, 292, 0, 1) }}>
        <SelectField value="Calendar schedule" />
        <label className="block text-[11px] text-muted-foreground">Time<input className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground" readOnly value={frame < 320 ? "09:00" : "10:00"} /></label>
        <label className="block text-[11px] text-muted-foreground">Timezone<input className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground" readOnly value="UTC" /></label>
        <button className={routineButton}>Save</button>{" "}<button className={routineButton}>Cancel</button>
      </div>}
    </div>
    {!editing && <div className="space-y-2"><h4 className="text-[11px] text-muted-foreground">Execution history</h4><p className="text-[12px] text-muted-foreground">No executions yet.</p></div>}
    </div>
  </section>;
};

const Options: React.FC<{ frame: number; name: string }> = ({ frame, name }) => <aside className="flex h-full min-h-0 flex-col border-l border-border bg-sidebar">
  <header className="flex shrink-0 items-center justify-between gap-2 px-3 pb-1 pt-3">
    <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">Options</h2><button className="rounded-md p-1 text-muted-foreground"><MoreHorizontal className="h-4 w-4" /></button>
  </header>
  <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-3 pb-3 pt-2">
    {frame < 210 ? <>
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
    </> : <RoutineDetail frame={frame} />}
  </div>
</aside>;

export const Agents: React.FC = () => {
  const frame = useCurrentFrame();
  const agent = frame < 42 ? AGENTS[2] : AGENTS[1];
  const focus = move(frame, 192, 204, 0, 1) - move(frame, 414, 432, 0, 1);
  const scale = 1 + focus * 0.32;
  const cursorVisible = (frame >= 18 && frame < 55) || (frame >= 179 && frame < 220);
  const cursorX = frame < 55 ? move(frame, 18, 38, 25, 10) : move(frame, 179, 202, 65, 88);
  const cursorY = frame < 55 ? move(frame, 18, 38, 62, 33) : move(frame, 179, 202, 78, 38);
  const click = frame < 55 ? Math.abs(frame - 42) < 5 : Math.abs(frame - 210) < 5;
  return <AppShell active="agents" title={frame < 150 ? "Your team. One workspace." : frame < 285 ? "Give every agent a routine." : "Set the schedule. Keep control."}
    subtitle={frame < 150 ? "Jarvis Agents" : "Chats · instructions · schedules"}>
    <div className="relative flex h-full min-h-0 flex-1 overflow-hidden bg-card" data-readme-agents>
      <style>{`[data-readme-agents] *, [data-readme-agents] *::before, [data-readme-agents] *::after { transition: none !important; animation: none !important; }`}</style>
      <div className="relative grid h-full min-h-0 w-full flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)]" style={{ gridTemplateRows: "minmax(0, 1fr)", transform: `scale(${scale})`, transformOrigin: "100% 45%" }}>
        <Roster selected={agent.name} />
        <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_minmax(280px,320px)] overflow-hidden rounded-tl-[12px] border-l border-border bg-background" style={{ gridTemplateRows: "minmax(0, 1fr)" }}>
          <Chat frame={frame} agent={agent} /><Options frame={frame} name={agent.name} />
        </div>
        {cursorVisible && <MousePointer2 fill="hsl(var(--foreground))" stroke="hsl(var(--background))" strokeWidth={1.7} size={30} style={{ position: "absolute", left: `${cursorX}%`, top: `${cursorY}%`, transform: `scale(${click ? 0.8 : 1})`, filter: "drop-shadow(0 2px 3px rgba(0,0,0,.4))" }} />}
      </div>
    </div>
  </AppShell>;
};
