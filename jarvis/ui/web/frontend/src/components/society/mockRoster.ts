/**
 * Sample roster — stands in for society.db until the M1 backend is bound.
 *
 * Every value is deliberately static sample data (fixed timestamps, no chat
 * sessions) and the roster rail shows a "sample data" badge, so the preview
 * never pretends to be live state (the loading-states rule: an empty or
 * mocked surface must say so, not invent numbers).
 *
 * The first row is the demo of the lead: Jarvis as the society's chief
 * executive — a human figure in a dark suit who owns the whole team and
 * delegates everything (maintainer direction 2026-09-01). Every row carries
 * a figure recipe so the card, the rail and the island agree on the look.
 */
import type { SocietyAgent } from "./data";
import type { FigureRecipe } from "./figures/figureRecipe";

/** 2026-08-20 12:00 UTC — a stable "recently active" anchor for sample rows. */
const SAMPLE_LAST_ACTIVE_MS = Date.UTC(2026, 7, 20, 12, 0, 0);
const SAMPLE_CREATED_MS = Date.UTC(2026, 7, 1, 9, 0, 0);

/** Jarvis is Gigi — the app's own pixel-ghost mascot, built as the spirit base. */
const LEAD_FIGURE: FigureRecipe = {
  contract: 1,
  archetype: "spirit",
  base: "gigi",
  parts: {},
  style: "spirit",
};

const SCOUT_FIGURE: FigureRecipe = {
  contract: 1,
  archetype: "biped",
  base: "rogue",
  parts: { back: "back-rogue-cape" },
  palette: {
    skin: "#d8a37c",
    hair: "#2a1d15",
    primary: "#c05b3c",
    secondary: "#efe0cd",
    accent: "#8fd0a0",
    shoes: "#2b2b2b",
  },
};

const ARCHIVIST_FIGURE: FigureRecipe = {
  contract: 1,
  archetype: "biped",
  base: "mage",
  parts: { hand_l: "hand_l-spellbook" },
  palette: {
    skin: "#e9c3a3",
    hair: "#6b6b6b",
    primary: "#5a7a4f",
    secondary: "#e8dcc4",
    accent: "#e8c46b",
    shoes: "#3a2a1e",
  },
  heightM: 1.7,
};

export const SAMPLE_ROSTER: SocietyAgent[] = [
  {
    agentId: "jarvis",
    name: "Jarvis",
    title: "Chief executive — leads and delegates the whole team",
    description: [
      "You are the lead of this agent society: its chief executive.",
      "You own the goals, not the tasks. Every request that reaches you is triaged,",
      "shaped into a clear assignment and handed to the orchestrator or specialist",
      "whose title fits; you never do a specialist's work yourself.",
      "You keep the team small, the hand-offs written (what was done, evidence, what",
      "remains, who owns the next step) and the spend inside the daily budget.",
      "Ask-tier actions always wait for the person; you never route around an approval.",
      "You speak for the team in the person's language and report outcomes, not activity.",
    ].join(" "),
    tier: "lead",
    provider: "anthropic",
    providerLabel: "Anthropic",
    model: "claude-sonnet-5",
    figure: LEAD_FIGURE,
    palette: { primary: "#1f2a44", secondary: "#f2f2ee", accent: "#c9a227" },
    grantMode: "all",
    effort: "",
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    lifecycle: "active",
    toolGrants: ["delegate-to-agent", "society-status", "approvals"],
    permissionCeiling: "ask",
    dailyBudgetUsd: 10,
    checkpoint: "idle",
    state: "idle",
    createdMs: SAMPLE_CREATED_MS,
    chatSessionId: null,
    routines: [
      {
        id: "jarvis-daily-standup",
        label: "Morning stand-up digest",
        schedule: "daily 08:00",
        nextFire: "2026-09-02T08:00:00Z",
      },
    ],
    stats: { runs: 42, totalCostUsd: 6.1, lastActiveMs: SAMPLE_LAST_ACTIVE_MS },
  },
  {
    agentId: "scout",
    name: "Scout",
    title: "Research orchestrator",
    description:
      "You own research. You break a question into sources, read broadly, keep evidence links and hand back a brief with what remains open.",
    tier: "orchestrator",
    provider: "anthropic",
    providerLabel: "Anthropic",
    model: "claude-sonnet-5",
    figure: SCOUT_FIGURE,
    palette: { primary: "#c05b3c", secondary: "#efe0cd", accent: "#8fd0a0" },
    grantMode: "all",
    effort: "",
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    lifecycle: "active",
    toolGrants: ["web_search", "web_fetch", "files_read", "knowledge_write"],
    permissionCeiling: "monitor",
    dailyBudgetUsd: 5,
    checkpoint: "desk",
    state: "working",
    createdMs: SAMPLE_CREATED_MS,
    chatSessionId: null,
    routines: [
      {
        id: "scout-morning-brief",
        label: "Morning research brief",
        schedule: "daily 07:00",
        nextFire: "2026-09-02T07:00:00Z",
      },
    ],
    stats: { runs: 128, totalCostUsd: 14.7, lastActiveMs: SAMPLE_LAST_ACTIVE_MS },
  },
  {
    agentId: "archivist",
    name: "Archivist",
    title: "Knowledge curator",
    description:
      "You own the shared memory. You turn finished work into short, sourced wiki notes under your namespace and propose what deserves promotion to shared knowledge.",
    tier: "specialist",
    provider: "ollama",
    providerLabel: "Ollama (local)",
    model: "qwen3:8b",
    figure: ARCHIVIST_FIGURE,
    palette: { primary: "#5a7a4f", secondary: "#e8dcc4", accent: "#e8c46b" },
    grantMode: "allowlist",
    effort: "",
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    lifecycle: "active",
    toolGrants: ["knowledge_write", "knowledge_search", "files_read"],
    permissionCeiling: "safe",
    dailyBudgetUsd: 0.5,
    checkpoint: "archive",
    state: "waiting",
    createdMs: SAMPLE_CREATED_MS,
    chatSessionId: null,
    routines: [
      {
        id: "archivist-nightly-curation",
        label: "Nightly curation pass",
        schedule: "daily 23:30",
        nextFire: "2026-09-01T23:30:00Z",
      },
    ],
    stats: { runs: 311, totalCostUsd: 0.9, lastActiveMs: SAMPLE_LAST_ACTIVE_MS },
  },
];
