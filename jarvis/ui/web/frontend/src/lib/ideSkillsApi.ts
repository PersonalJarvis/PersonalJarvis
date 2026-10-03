// REST client for the Agentic IDE's skill library (`/api/agentic-ide/skills`).
// Plain same-origin fetch with `no-store`, like the rest of the IDE's clients:
// WebView2 would otherwise serve a stale list after an edit.

/** The palette slots a skill can wear; the UI maps each onto theme tokens. */
export const SKILL_HUES = ["blue", "violet", "teal", "amber", "rose", "green", "magenta", "slate"] as const;
export type SkillHue = (typeof SKILL_HUES)[number];

/** The glyphs a skill can wear; "auto" picks one from the title. Same order as the backend. */
export const SKILL_ICONS = [
  "auto", "doc", "plan", "code", "bug", "flask", "review", "shield", "refactor", "book", "list", "git", "terminal",
  "palette", "data", "perf", "rocket",
] as const;
export type SkillIcon = (typeof SKILL_ICONS)[number];

export interface IdeSkill {
  id: string;
  title: string;
  /** The Markdown pasted into a terminal, verbatim. */
  content: string;
  /** One line: the frontmatter description or the first prose line. */
  description: string;
  hue: SkillHue;
  icon: SkillIcon;
  created_at: string;
  updated_at: string;
  use_count: number;
  last_used_at: string | null;
}

export interface IdeSkillDraft {
  title: string;
  content: string;
  description?: string;
  hue?: SkillHue;
  icon?: SkillIcon;
}

const BASE = "/api/agentic-ide/skills";

async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body?.detail === "string" && body.detail) return body.detail;
  } catch {
    /* not JSON: the status line below is the best there is */
  }
  return `${res.status} ${res.statusText}`.trim();
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
  });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as T;
}

export async function listIdeSkills(): Promise<IdeSkill[]> {
  return (await call<{ skills: IdeSkill[] }>("")).skills;
}

export function createIdeSkill(draft: IdeSkillDraft): Promise<IdeSkill> {
  return call<IdeSkill>("", { method: "POST", body: JSON.stringify(draft) });
}

export function updateIdeSkill(id: string, patch: Partial<IdeSkillDraft>): Promise<IdeSkill> {
  return call<IdeSkill>(`/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) });
}

export async function deleteIdeSkill(id: string): Promise<void> {
  await call<{ removed: boolean }>(`/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function markIdeSkillUsed(id: string): Promise<IdeSkill> {
  return call<IdeSkill>(`/${encodeURIComponent(id)}/used`, { method: "POST" });
}

export async function reorderIdeSkills(ids: string[]): Promise<IdeSkill[]> {
  return (await call<{ skills: IdeSkill[] }>("/order", { method: "PUT", body: JSON.stringify({ ids }) })).skills;
}

export function deriveIdeSkillFields(content: string, filename = ""): Promise<{ title: string; description: string }> {
  return call("/derive", { method: "POST", body: JSON.stringify({ content, filename }) });
}
