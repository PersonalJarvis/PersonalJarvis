/**
 * Share an agent as a template, and install one somebody else shared.
 *
 * Wire types mirror jarvis/ui/web/society_routes.py ("templates" section) and
 * jarvis/society/agent_template.py. The template is always BUILT by the
 * server from the roster row plus the public-version edits; this module never
 * sends a template of its own to publish, so what leaves the machine is
 * exactly what the server's scrubber let through.
 */
import { fill, translate } from "@/i18n";

export type ShareFindingKind = "secret" | "email" | "path" | "phone" | "address";

/** One thing taken out of the text before sharing — named, never repeated. */
export interface ShareFinding {
  kind: ShareFindingKind;
  /** "instructions" | "title" | "summary" */
  field: string;
  /** First and last characters of what was removed, so its author recognises it. */
  hint: string;
}

/** The template itself: the agent's design, nothing of its operation. */
export interface AgentTemplateWire {
  schema: 1;
  name: string;
  title: string;
  instructions: string;
  tier: "specialist" | "orchestrator";
  effort: string;
  focus: string[];
  grant_mode: "all" | "allowlist";
  grants: string[];
  denies: string[];
  skills: string[] | null;
  require_approval: string[];
  knowledge_scope: "shared" | "own";
  avatar: Record<string, unknown>;
}

/** How the template is listed in the marketplace. */
export interface ShareListing {
  /** The marketplace name (a-z 0-9 - .) — the id people install by. */
  name: string;
  title: string;
  /** The store-card summary. */
  description: string;
  categories: string[];
  version: string;
}

export interface SharePublished {
  name: string;
  version: string;
  url?: string | null;
}

export interface ShareDraftWire {
  template: AgentTemplateWire;
  listing: ShareListing;
  findings: ShareFinding[];
  /** The agent's name when it wrote the public version itself; "" otherwise. */
  polished_by: string;
  published: SharePublished | null;
  /** Exactly what Publish files: `{kind: "agent", name, version, …, agent}`. */
  submission: Record<string, unknown>;
  /** Why it cannot be published yet; empty when it can. */
  errors: string[];
}

export interface ShareDraftEdits {
  summary?: string;
  title?: string;
  categories?: string[];
  listing_name?: string;
  version?: string;
  reset?: boolean;
}

export interface PublishShareResult {
  ok: boolean;
  name: string;
  version: string;
  url?: string | null;
  issue_url?: string | null;
  pr_url?: string | null;
  install?: { cli: string; runner: string; prompt: string } | null;
}

export interface InstallTemplateResult {
  agent: { agent_id: string; name: string };
  created: boolean;
  renamed_from: string | null;
}

/** A refusal from the server, with the field it concerns when there is one. */
export class ShareError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly field: string | null = null,
  ) {
    super(message);
  }
}

export const shareDraftKey = (agentId: string) => ["society", "share-template", agentId] as const;

/** The registry's issue form — the browser route when the app may not file it. */
export const MARKETPLACE_ISSUE_FORM_URL =
  "https://github.com/PersonalJarvis/marketplace/issues/new?template=publish.yml";

async function readError(res: Response): Promise<ShareError> {
  const body = (await res.json().catch(() => ({}))) as {
    detail?: string | { error?: string; detail?: string; field?: string | null };
  };
  const detail = body.detail;
  if (typeof detail === "string") return new ShareError(detail, res.status);
  const message = detail?.error ?? detail?.detail ?? fill(translate("common.request_failed"), { status: res.status });
  return new ShareError(message, res.status, detail?.field ?? null);
}

const agentUrl = (agentId: string) => `/api/society/agents/${encodeURIComponent(agentId)}/template`;

export async function fetchShareDraft(agentId: string): Promise<ShareDraftWire> {
  const res = await fetch(agentUrl(agentId), { cache: "no-store" });
  if (!res.ok) throw await readError(res);
  return res.json();
}

export async function saveShareDraft(
  agentId: string,
  edits: ShareDraftEdits,
): Promise<ShareDraftWire> {
  const res = await fetch(agentUrl(agentId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(edits),
  });
  if (!res.ok) throw await readError(res);
  return res.json();
}

export async function publishShareDraft(agentId: string): Promise<PublishShareResult> {
  const res = await fetch(`${agentUrl(agentId)}/publish`, { method: "POST" });
  if (!res.ok) throw await readError(res);
  return res.json();
}

/** Create a NEW agent from a template (a file, pasted JSON, or a submission). */
export async function installAgentTemplate(
  template: Record<string, unknown>,
): Promise<InstallTemplateResult> {
  const res = await fetch("/api/society/templates/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template }),
  });
  if (!res.ok) throw await readError(res);
  return res.json();
}

/** Whether ``name@version`` is in the live marketplace feed yet. */
export async function fetchLiveStatus(name: string, version: string): Promise<boolean> {
  const params = new URLSearchParams({ name, version, force: "true" });
  const res = await fetch(`/api/marketplace/publish/status?${params}`, { cache: "no-store" });
  if (!res.ok) return false;
  const body = (await res.json()) as { live?: boolean };
  return Boolean(body.live);
}

/** A picked file's text — FileReader, which every WebView and test DOM has. */
export function readFileText(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("the file could not be read"));
    reader.readAsText(file);
  });
}

/** The file name an exported template is saved under. */
export function templateFileName(listing: Pick<ShareListing, "name">): string {
  return `${listing.name || "agent"}.agent.json`;
}

/** Hand the template to the person as a file (the Export button). */
export function downloadTemplate(fileName: string, json: string): void {
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
