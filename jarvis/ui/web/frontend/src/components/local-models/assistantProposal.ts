/**
 * The setup assistant's proposal contract, on the client side.
 *
 * The assistant (an agent-chat session on the `local-models` surface) ends a
 * planning turn with ONE fenced ```jarvis-proposal JSON block — the plan the
 * user confirms in a single click. This module finds that block in the
 * assistant's prose, validates it into a `Proposal`, hashes it so the
 * confirmation names exactly what was shown, and decides whether a later
 * `approval_required` event asks for something the user already confirmed
 * (so the card can be answered for them). `reduce.ts` is untouched: the
 * proposal is read from the finished text, never from the event stream.
 */

export type ProposalStepKind =
  | "install_ollama"
  | "pull"
  | "set_role"
  | "set_options"
  | "apply_voice_stack"
  | "test";

/** How well-trodden the model behind a step is; `null` when the step has no model. */
export type ProvenLabel = "proven" | "new_little_tested" | "stale";

export interface ProposalStep {
  id: string;
  kind: ProposalStepKind;
  model?: string;
  role?: string;
  options?: Record<string, unknown>;
  size_gb?: number;
  fit?: string;
  proven: ProvenLabel | null;
  label: string;
}

export interface BrainSwitch {
  provider: string;
  why: string;
}

export interface Proposal {
  version: 1;
  steps: ProposalStep[];
  brain_switch: BrainSwitch | null;
  notes: string[];
}

const STEP_KINDS: ReadonlySet<string> = new Set([
  "install_ollama",
  "pull",
  "set_role",
  "set_options",
  "apply_voice_stack",
  "test",
]);

const FENCE_OPEN = /```jarvis-proposal[^\n]*\n/g;

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.trim() ? v.trim() : undefined;
}

function provenOf(v: unknown): ProvenLabel | null {
  if (v === true || v === "proven") return "proven";
  if (v === false || v === "new_little_tested") return "new_little_tested";
  if (v === "stale") return "stale";
  return null;
}

function stepOf(raw: unknown, index: number): ProposalStep | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const kind = str(r.kind);
  if (!kind || !STEP_KINDS.has(kind)) return null;
  const model = str(r.model);
  const role = str(r.role);
  const id = str(r.id) ?? `${kind}-${index + 1}`;
  const label = str(r.label) ?? [kind, role, model].filter(Boolean).join(" ");
  const step: ProposalStep = { id, kind: kind as ProposalStepKind, proven: provenOf(r.proven), label };
  if (model) step.model = model;
  if (role) step.role = role;
  if (r.options && typeof r.options === "object" && !Array.isArray(r.options)) {
    step.options = r.options as Record<string, unknown>;
  }
  if (typeof r.size_gb === "number" && Number.isFinite(r.size_gb)) step.size_gb = r.size_gb;
  const fit = str(r.fit);
  if (fit) step.fit = fit;
  return step;
}

/** Validates one parsed JSON value into a Proposal; null when it is not one. */
export function proposalFromJson(value: unknown): Proposal | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  if (v.version !== 1 && v.version !== "1") return null;
  if (!Array.isArray(v.steps)) return null;
  const steps = v.steps
    .map((s, i) => stepOf(s, i))
    .filter((s): s is ProposalStep => s !== null);
  if (steps.length === 0) return null;
  // Duplicate ids would make "Execute steps: …" ambiguous; suffix the repeats.
  const seen = new Set<string>();
  for (const step of steps) {
    let id = step.id;
    let n = 2;
    while (seen.has(id)) id = `${step.id}-${n++}`;
    seen.add(id);
    step.id = id;
  }
  let brain_switch: BrainSwitch | null = null;
  if (v.brain_switch && typeof v.brain_switch === "object") {
    const b = v.brain_switch as Record<string, unknown>;
    const provider = str(b.provider);
    if (provider) brain_switch = { provider, why: str(b.why) ?? "" };
  }
  const notes = Array.isArray(v.notes)
    ? v.notes.filter((n): n is string => typeof n === "string" && n.trim().length > 0)
    : [];
  return { version: 1, steps, brain_switch, notes };
}

/**
 * The JSON text between a fence opener and its closer. A model that forgot
 * the closing fence (or wrote prose after it) still gets parsed: the block
 * ends at the last `}` that makes the object balance.
 */
function jsonBodyAfter(text: string, start: number): string | null {
  const rest = text.slice(start);
  const close = rest.indexOf("```");
  const candidate = close >= 0 ? rest.slice(0, close) : rest;
  const first = candidate.indexOf("{");
  if (first < 0) return null;
  // Walk to the matching brace so trailing prose inside an unclosed fence is
  // dropped rather than breaking JSON.parse.
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = first; i < candidate.length; i++) {
    const ch = candidate[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return candidate.slice(first, i + 1);
    }
  }
  return null;
}

/**
 * Finds the LAST ```jarvis-proposal block in `text` and returns it as a
 * Proposal, or null when there is none or it does not parse. Later blocks
 * win because a corrected plan follows the plan it corrects.
 */
export function proposalFromText(text: string): Proposal | null {
  if (!text) return null;
  const starts: number[] = [];
  for (const m of text.matchAll(FENCE_OPEN)) starts.push(m.index + m[0].length);
  for (let i = starts.length - 1; i >= 0; i--) {
    const body = jsonBodyAfter(text, starts[i]);
    if (!body) continue;
    try {
      const parsed = proposalFromJson(JSON.parse(body) as unknown);
      if (parsed) return parsed;
    } catch {
      // A malformed block is skipped; an earlier valid one may still apply.
    }
  }
  return null;
}

/** Stable key order so the same plan always hashes the same. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** Short, stable FNV-1a hash (8 hex chars) over the proposal's steps and switch. */
export function proposalHash(p: Proposal): string {
  const text = canonical({ version: p.version, steps: p.steps, brain_switch: p.brain_switch });
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

/** The one-line confirmation the user's click sends to the assistant. */
export function confirmationMessage(p: Proposal, steps: ProposalStep[]): string {
  return `Execute steps: ${steps.map((s) => s.id).join(", ")} (proposal v${p.version}, hash ${proposalHash(p)})`;
}

/** Ollama treats `name` and `name:latest` as the same model; so do we. */
export function normaliseModel(name: string | undefined): string {
  const n = (name ?? "").trim().toLowerCase();
  return n.endsWith(":latest") ? n.slice(0, -":latest".length) : n;
}

function argString(input: unknown, key: string): string | undefined {
  if (!input || typeof input !== "object") return undefined;
  const v = (input as Record<string, unknown>)[key];
  return typeof v === "string" ? v : undefined;
}

/** What an `approval_required` event carries that the matcher looks at. */
export interface ApprovalRequest {
  /** The tool the runner wants to call (`lm_pull`, `lm_set_role`, …). */
  name: string;
  /** Its arguments, as sent by the runner. */
  input: unknown;
}

/**
 * True when the approval asks for something the user confirmed on the
 * proposal card — that model's pull, that role→model write, the install…
 * Anything not covered (a different model, a tool the plan never named)
 * returns false and the ordinary approval card stays on screen.
 */
export function matchesConfirmedStep(
  approval: ApprovalRequest,
  confirmed: readonly ProposalStep[],
): boolean {
  if (confirmed.length === 0) return false;
  const model = normaliseModel(argString(approval.input, "model"));
  const role = (argString(approval.input, "role") ?? "").trim().toLowerCase();
  const has = (kind: ProposalStepKind) => confirmed.some((s) => s.kind === kind);
  switch (approval.name) {
    case "lm_install_ollama":
      return has("install_ollama");
    case "lm_start_server":
      // Starting the server is implied by any confirmed step: nothing in the
      // plan works without it.
      return true;
    case "lm_pull":
      return confirmed.some((s) => s.kind === "pull" && normaliseModel(s.model) === model);
    case "lm_set_role":
      return confirmed.some(
        (s) =>
          s.kind === "set_role" &&
          (s.role ?? "").toLowerCase() === role &&
          normaliseModel(s.model) === model,
      );
    case "lm_set_model_options":
      return confirmed.some((s) => s.kind === "set_options" && normaliseModel(s.model) === model);
    case "lm_apply_voice_stack":
    case "lm_install_voice_server":
      return has("apply_voice_stack");
    case "lm_test_plan":
    case "lm_test":
      return has("test");
    default:
      return false;
  }
}

/**
 * `text` without its ```jarvis-proposal block(s): the plan is shown as the
 * checklist card, so the raw JSON has no business in the chat bubble. A block
 * with no closing fence (a cut-off stream) is stripped to the end of the text.
 */
export function stripProposalBlocks(text: string): string {
  const re = /```jarvis-proposal[^\n]*\n[\s\S]*?(?:```|$)/g;
  return text.replace(re, "").replace(/\n{3,}/g, "\n\n").trim();
}
