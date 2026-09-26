import React from "react";
import { Easing, Img, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import {
  Brain, Check, ChevronDown, ChevronRight, CircleDashed, Clock,
  Loader2, Maximize2, Mic, MoreHorizontal, MousePointer2, Plus, Search, Send, Square,
} from "lucide-react";
import { Badge } from "@app/components/ui/badge";
import { Button } from "@app/components/ui/button";
import { AppShell } from "./shared";
import { AgentSymbol } from "./identity/AgentSymbol";
import { defaultCompanion } from "./identity/appearance";
// Exact source asset: jarvis/ui/web/frontend/src/assets/gigi-companion-avatar.png.
// SHA-256: 7B9E3128AD160644B5ECA8EA223D614EABD5BA029A8CD825D15898AC791E53E0.
import gigiCompanionMark from "./assets/gigi-companion-avatar.png";

/**
 * Deterministic visual adapter for the actual Agents workspace, not a second UI.
 * Layout/classes: society/card/AgentCardOverlay.tsx, roster/RosterRail.tsx,
 * chat/AgentChatPanel.tsx, card/OptionsRail.tsx, agentchat/WorkTrace.tsx.
 * The live components own sockets, stores and mutation hooks; this adapter
 * keeps their rendered structure and supplies explicitly illustrative data.
 * Badge and Button are the production components. No API runs here. Trace text
 * is an illustrative task-status summary, never private model reasoning.
 */
const AGENTS = [
  { name: "Jarvis", title: "Lead", tier: "lead" },
  { name: "Atlas", title: "Planning & research", tier: "specialist" },
  { name: "Nova", title: "Writing & review", tier: "specialist" },
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

/**
 * Current jarvis/ui/web/frontend/src/components/society/AgentSwatch.tsx structure.
 * Uses the snapshotted vectors; Remotion Img waits for the source avatar to load.
 */
const AgentIdentity: React.FC<{ agent: DemoAgent; size: number }> = ({ agent, size }) => (
  <span aria-hidden className="relative inline-flex shrink-0 items-center justify-center select-none" style={{ width: size, height: size }}>
    {agent.tier === "lead"
      ? <Img data-agent-mascot="gigi" src={gigiCompanionMark} alt="" draggable={false} width={size} height={size} className="h-full w-full object-contain" />
      : <AgentSymbol {...defaultCompanion(agent.name)} size={size} />}
  </span>
);

const Roster: React.FC<{ selected: string; busy: boolean; seconds: number }> = ({ selected, busy, seconds }) => (
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
        <div className="flex flex-col items-center gap-1.5 rounded-xl bg-secondary/50 px-5 py-3 text-center" data-testid="society-lead-hero">
          <button type="button" aria-label="Open profile of Jarvis" className="relative rounded-full"><AgentIdentity agent={AGENTS[0]} size={56} /></button>
          <button type="button" className="flex max-w-full items-center justify-center gap-1.5 rounded-md"><span className="truncate text-sm font-medium text-foreground">Jarvis</span><Badge variant="secondary" className="shrink-0 px-1.5 py-0 text-xs">Lead</Badge></button>
        </div>
      </div>
      <div className="mx-3 mb-1 border-t border-border/60" />
      <ul className="flex flex-col gap-0.5 px-2 pb-3">
        {AGENTS.slice(1).map((agent) => <li key={agent.name}>
          <div className={`flex w-full items-center rounded-md px-2 text-left ${selected === agent.name ? "bg-secondary" : ""}`}>
            <button type="button" aria-label={`Open profile of ${agent.name}`} className="shrink-0 rounded-full"><AgentIdentity agent={agent} size={34} /></button>
            <button type="button" aria-current={selected === agent.name ? "true" : undefined} className="flex min-w-0 flex-1 select-none items-center gap-2.5 rounded-md py-2 pl-2.5 text-left">
            <span className="min-w-0 flex-1"><span className="block text-sm font-medium text-foreground">{agent.name}</span><span className="block truncate text-xs text-muted-foreground">{agent.title}</span></span>
            {selected === agent.name && busy
              ? <span className="grid h-4 w-4 shrink-0 place-items-center text-muted-foreground"><Loader2 className="h-3 w-3" style={{ transform: `rotate(${seconds * 300}deg)` }} /></span>
              : <span className="h-2 w-2 shrink-0 rounded-full bg-muted-foreground/50" />}
            </button>
          </div>
        </li>)}
      </ul>
    </div>
  </aside>
);

/** All timings are seconds, independent of render frame rate or clip duration. */
export const AGENTS_TIMELINE = {
  typeStart: 0.6,
  typeEnd: 2.1,
  send: 2.3,
  thinkingStart: 2.5,
  answerStart: 6.5,
  answerEnd: 10.5,
  holdEnd: 14,
} as const;

const BRIEF = "Plan the next release. Ask before changing any files.";
const STATUS_LINES = [
  { start: 2.55, text: "Preparing a release-plan outline." },
  { start: 3.7, text: "Keeping changes behind your approval." },
  { start: 4.95, text: "Organising scope, owners and verification." },
] as const;
const ANSWER_PARTS = [
  "Draft release plan",
  "Scope — agree the changes, acceptance checks and exclusions.",
  "Owners — Atlas drafts the task list; Nova prepares release notes.",
  "Verify — run the relevant tests and review the diff before release.",
  "Shall I start with a draft task list? I will wait for approval before changing files.",
] as const;

const UserMessage: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) =>
  <div className="flex min-w-0 max-w-[min(85%,42rem)] flex-col items-end gap-1 self-end" style={style}>
    <div className="min-w-0 rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-sm leading-relaxed text-foreground [overflow-wrap:anywhere]">{children}</div>
  </div>;

/** The production Prose/TraceGroups answer shape with frame-driven text deltas. */
const StreamingAnswer: React.FC<{ seconds: number }> = ({ seconds }) => {
  const total = ANSWER_PARTS.reduce((length, part) => length + part.length, 0);
  let remaining = Math.floor(interpolate(seconds,
    [AGENTS_TIMELINE.answerStart, AGENTS_TIMELINE.answerEnd], [0, total],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  const visible = ANSWER_PARTS.map(part => {
    const value = part.slice(0, Math.max(0, remaining));
    remaining -= part.length;
    return value;
  });
  return <div className="min-w-0 rounded-2xl rounded-bl-md bg-secondary px-4 py-2.5">
    <div className="prose prose-neutral max-w-none text-sm leading-relaxed text-foreground dark:prose-invert [overflow-wrap:anywhere]">
      {visible[0] && <p className="mb-2 font-semibold">{visible[0]}</p>}
      {visible.slice(1, 4).some(Boolean) && <ol className="my-1.5 list-decimal space-y-1 pl-5">
        {visible.slice(1, 4).map((text, index) => text && <li key={index}>{text}</li>)}
      </ol>}
      {visible[4] && <p className="mt-3">{visible[4]}</p>}
    </div>
  </div>;
};

/**
 * Adapter of current WorkTrace.tsx Disclosure/ReasoningBody and conversation
 * footer. Production useClock reads wall time, so elapsed time, pulse and
 * spinner instead use the Remotion frame. The existing thought disclosure
 * remains expanded as if selected by the viewer after completion.
 * Only a draft answer finishes; no tool or file change is simulated.
 */
const PlanningTrace: React.FC<{ seconds: number }> = ({ seconds }) => {
  const thinking = seconds < AGENTS_TIMELINE.answerStart;
  const complete = seconds >= AGENTS_TIMELINE.answerEnd;
  const thoughtDuration = Math.min(4, Math.max(0, seconds - AGENTS_TIMELINE.thinkingStart));
  const turnDuration = Math.min(8.2, Math.max(0, seconds - AGENTS_TIMELINE.send));
  const summary = STATUS_LINES.map(line => typed(line.text, seconds, line.start, 0.9)).filter(Boolean);
  return <div className="min-w-0 w-full max-w-xl self-start space-y-0.5" data-testid="work-trace" data-state={complete ? "done" : "running"} data-conversation
    style={{ opacity: move(seconds, 2.5, 2.75, 0, 1), transform: `translateY(${move(seconds, 2.5, 2.85, 8, 0)}px)` }}>
    <div className="w-full text-xs [&_button]:text-xs">
      <div className="min-w-0 text-muted-foreground">
        <button type="button" aria-expanded="true"
          className="group/trace flex w-full min-w-0 items-start gap-2.5 rounded-md py-2 text-left text-[13px] leading-6">
          <Brain className="mt-0.5 h-4 w-4 shrink-0" style={{ opacity: thinking ? 0.58 + 0.42 * (0.5 + 0.5 * Math.sin(seconds * 7)) : 1 }} />
          <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{thinking ? "Thinking" : "Thought"} for {thoughtDuration.toFixed(1)}s</span>
          <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 rotate-90 opacity-50" />
        </button>
        <div className="mb-2 ml-[7px] min-w-0 border-l border-border/70 pb-1 pl-5">
          <div data-testid="reasoning-body" className="prose prose-sm max-h-64 max-w-none overflow-hidden text-xs leading-6 text-muted-foreground dark:prose-invert [overflow-wrap:anywhere]">
            {summary.map((line, index) => <p key={index} className="my-1">{line}</p>)}
          </div>
        </div>
      </div>
    </div>
    {seconds >= AGENTS_TIMELINE.answerStart && <StreamingAnswer seconds={seconds} />}
    <div role="status" className="flex flex-wrap items-center gap-2 px-1 pb-2 pt-1 text-xs text-muted-foreground">
      {complete ? <Check className="h-3.5 w-3.5" /> : <CircleDashed className="h-3.5 w-3.5" style={{ transform: `rotate(${seconds * 300}deg)` }} />}
      <span>{complete ? "Done" : "Working"}</span>
      <span className="tabular-nums">{turnDuration.toFixed(1)}s</span>
    </div>
  </div>;
};

const Chat: React.FC<{ seconds: number; agent: DemoAgent }> = ({ seconds, agent }) => {
  const draft = typed(BRIEF, seconds, AGENTS_TIMELINE.typeStart, AGENTS_TIMELINE.typeEnd - AGENTS_TIMELINE.typeStart);
  const submitted = seconds >= AGENTS_TIMELINE.send;
  const busy = submitted && seconds < AGENTS_TIMELINE.answerEnd;
  return <section className="flex h-full min-h-0 flex-col bg-background">
    <div className="min-h-0 flex-1 overflow-hidden px-6 py-5">
      <div className="mx-auto flex w-full min-w-0 flex-col gap-3">
        <p className="my-4 text-center text-[11px] text-muted-foreground">Today 09:00</p>
        <UserMessage>Help me prepare the next release.</UserMessage>
        <div className="w-full max-w-xl self-start rounded-2xl rounded-bl-md bg-secondary px-4 py-2.5 text-sm leading-relaxed text-foreground">
          Share the goal and constraints. I can draft a plan for your review.
        </div>
        <p className="my-4 text-center text-[11px] text-muted-foreground">Today 09:02</p>
        {submitted && <UserMessage style={{ opacity: move(seconds, 2.3, 2.45, 0, 1), transform: `translateY(${move(seconds, 2.3, 2.55, 12, 0)}px)` }}>{BRIEF}</UserMessage>}
        {seconds >= AGENTS_TIMELINE.thinkingStart && <PlanningTrace seconds={seconds} />}
      </div>
    </div>
    <div className="shrink-0 px-6 pb-4 pt-2">
      <div className="relative mx-auto flex w-full min-w-0 items-end gap-1 rounded-[24px] border border-border bg-secondary px-2 py-1.5">
        <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground"><Plus className="h-4 w-4" /></button>
        <div className="min-h-8 min-w-0 flex-1 whitespace-pre-wrap px-1 py-1.5 text-sm leading-5 text-foreground">{!submitted && draft ? <>{draft}<span style={{ opacity: Math.floor(seconds * 2.5) % 2 ? 0 : 1 }}>|</span></> : <span className="text-muted-foreground">Message {agent.name}</span>}</div>
        <div className="flex h-8 min-w-0 max-w-[40%] shrink-0 items-center"><button disabled={busy} className="flex max-w-full items-center gap-1.5 rounded-full px-2 py-1 text-xs text-muted-foreground disabled:opacity-50"><span className="truncate">Jarvis&apos; brain</span><ChevronDown className="h-3 w-3 shrink-0" /></button></div>
        <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted-foreground"><Mic className="h-4 w-4" /></button>
        {busy ? <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-foreground text-background" aria-label="Stop"><Square className="h-3.5 w-3.5" /></button>
          : <button className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground" style={{ opacity: !submitted && draft ? 1 : 0.4 }} aria-label="Send"><Send className="h-3.5 w-3.5" /></button>}
      </div>
    </div>
  </section>;
};

const Options: React.FC<{ name: string }> = ({ name }) => <aside className="flex h-full min-h-0 flex-col border-l border-border bg-sidebar">
  <header className="flex shrink-0 items-center justify-between gap-2 px-3 pb-1 pt-3">
    <h2 className="font-display text-sm font-semibold tracking-tight text-foreground">Options</h2><button className="rounded-md p-1 text-muted-foreground"><MoreHorizontal className="h-4 w-4" /></button>
  </header>
  <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-3 pb-3 pt-2">
    <button type="button" data-testid="edit-agent-appearance" className="shrink-0 rounded-lg border border-border px-3 py-2 text-left text-sm font-medium text-foreground">Character &amp; companion</button>
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
</aside>;

export const Agents: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const seconds = frame / fps;
  const agent = AGENTS[1];
  const busy = seconds >= AGENTS_TIMELINE.send && seconds < AGENTS_TIMELINE.answerEnd;
  const pointerVisible = seconds >= 1.8 && seconds < 2.6;
  const pointerX = move(seconds, 1.8, 2.2, 77, 84.9);
  const pointerY = move(seconds, 1.8, 2.2, 88, 96.9);
  const click = seconds >= 2.27 && seconds < 2.36;
  return <AppShell active="agents">
    <div className="relative h-full min-h-0 flex-1 overflow-hidden bg-card" data-readme-agents>
      <style>{`[data-readme-agents] *, [data-readme-agents] *::before, [data-readme-agents] *::after { transition: none !important; animation: none !important; }`}</style>
      <div className="relative grid h-full min-h-0 w-full" style={{ gridTemplateColumns: "12% minmax(0, 1fr)", gridTemplateRows: "minmax(0, 1fr)" }}>
        <Roster selected={agent.name} busy={busy} seconds={seconds} />
        <div className="grid min-h-0 overflow-hidden rounded-tl-[12px] border-l border-border bg-background" style={{ gridTemplateColumns: "minmax(0, 1fr) 14.77%", gridTemplateRows: "minmax(0, 1fr)" }}>
          <Chat seconds={seconds} agent={agent} /><Options name={agent.name} />
        </div>
        {pointerVisible && <MousePointer2 fill="hsl(var(--foreground))" stroke="hsl(var(--background))" strokeWidth={1.7} size={24}
          style={{ position: "absolute", left: `${pointerX}%`, top: `${pointerY}%`, opacity: 1 - move(seconds, 2.4, 2.6, 0, 1), transform: `scale(${click ? 0.8 : 1})`, filter: "drop-shadow(0 2px 3px rgba(0,0,0,.4))" }} />}
      </div>
    </div>
  </AppShell>;
};
