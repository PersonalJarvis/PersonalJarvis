/**
 * Browse a GitHub account's public repositories for publishable packages.
 *
 * Everything runs client-side against api.github.com (CORS-enabled,
 * unauthenticated — public data only, ~60 requests/hour per IP, plenty for a
 * hand-driven import). The flow mirrors what the folder drop does, one level
 * earlier: pick a repo, scan its tree for plugin.json / SKILL.md, fetch just
 * those files, and hand back the same shape `folderToDraft` consumes.
 *
 * No token is ever used here: the publisher token in the keyring is identity
 * for the submit endpoint, and this browser is only reading public files.
 */

const API = "https://api.github.com";
const UA_HEADERS = { Accept: "application/vnd.github+json" };

export interface RepoSummary {
  full_name: string;
  description: string | null;
  default_branch: string;
  pushed_at: string | null;
  stargazers_count: number;
}

export interface RepoCandidate {
  /** Directory the package lives in, "" for the repo root. */
  dir: string;
  kind: "plugin" | "skill";
  /** Paths (relative to repo root) that will be fetched on import. */
  paths: string[];
}

export interface RepoScan {
  candidates: RepoCandidate[];
  /** GitHub's tree API stopped before listing every file (huge repo) — the
   *  scan may have missed a package that lives deep in the tree. */
  truncated: boolean;
}

async function gh<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { headers: UA_HEADERS });
  if (res.status === 403 || res.status === 429) {
    throw new Error("GitHub rate limit reached — wait a few minutes and try again.");
  }
  if (res.status === 404) throw new Error("Not found on GitHub — check the name.");
  if (!res.ok) throw new Error(`GitHub request failed (${res.status})`);
  return (await res.json()) as T;
}

function toRepoSummary(r: Record<string, unknown>): RepoSummary {
  return {
    full_name: String(r.full_name ?? ""),
    description: typeof r.description === "string" ? r.description : null,
    default_branch: String(r.default_branch ?? "main"),
    pushed_at: typeof r.pushed_at === "string" ? r.pushed_at : null,
    stargazers_count: Number(r.stargazers_count ?? 0),
  };
}

/** Public repos of a user, most recently pushed first. */
export async function listRepos(login: string): Promise<RepoSummary[]> {
  const raw = await gh<Array<Record<string, unknown>>>(
    `/users/${encodeURIComponent(login)}/repos?sort=pushed&per_page=50`,
  );
  return raw.map(toRepoSummary);
}

/**
 * One repo's summary — used to resolve the real default branch before
 * scanning. The git trees API takes a branch name or SHA, not the literal
 * string "HEAD", so a "owner/repo" or URL paste must look this up first
 * instead of guessing a branch name.
 */
export async function getRepo(owner: string, repo: string): Promise<RepoSummary> {
  const raw = await gh<Record<string, unknown>>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
  );
  return toRepoSummary(raw);
}

/** Accepts "owner/repo" or a full github.com URL; null when unparseable. */
export function parseRepoRef(text: string): { owner: string; repo: string } | null {
  const trimmed = text.trim().replace(/\.git$/, "");
  const url = /github\.com\/([^/\s]+)\/([^/\s#?]+)/.exec(trimmed);
  if (url) return { owner: url[1], repo: url[2] };
  const plain = /^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/.exec(trimmed);
  if (plain) return { owner: plain[1], repo: plain[2] };
  return null;
}

/**
 * Scan a repo's file tree for package roots.
 *
 * A directory containing plugin.json is a plugin candidate (its mcp.json,
 * skills/ and usage card ride along). A SKILL.md whose directory is not
 * inside a plugin's skills/ tree is a standalone skill candidate.
 */
export async function scanRepo(owner: string, repo: string, branch: string): Promise<RepoScan> {
  const tree = await gh<{ tree: Array<{ path: string; type: string; size?: number }>; truncated?: boolean }>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/git/trees/${encodeURIComponent(branch)}?recursive=1`,
  );
  const paths = tree.tree.filter((t) => t.type === "blob").map((t) => t.path);
  const pathSet = new Set(paths);

  const pluginDirs = paths
    .filter((p) => p === "plugin.json" || p.endsWith("/plugin.json"))
    .map((p) => (p === "plugin.json" ? "" : p.slice(0, -"/plugin.json".length)));

  const candidates: RepoCandidate[] = pluginDirs.map((dir) => {
    const prefix = dir ? `${dir}/` : "";
    const wanted = [
      `${prefix}plugin.json`,
      `${prefix}mcp.json`,
      `${prefix}io.github.personaljarvis/usage-card.md`,
    ].filter((p) => pathSet.has(p));
    const skillMds = paths.filter((p) => p.startsWith(`${prefix}skills/`) && p.endsWith("/SKILL.md"));
    return { dir, kind: "plugin", paths: [...wanted, ...skillMds.slice(0, 10)] };
  });

  const insidePlugin = (p: string) =>
    pluginDirs.some((dir) => p.startsWith(dir ? `${dir}/` : "") && p.includes("skills/"));
  for (const p of paths) {
    if (!(p === "SKILL.md" || p.endsWith("/SKILL.md"))) continue;
    if (insidePlugin(p)) continue;
    const dir = p === "SKILL.md" ? "" : p.slice(0, -"/SKILL.md".length);
    candidates.push({ dir, kind: "skill", paths: [p] });
    if (candidates.length >= 40) break;
  }
  return { candidates, truncated: tree.truncated === true };
}

/** Fetch one file's text via the contents API (base64, UTF-8 decoded). */
export async function fetchRepoFile(
  owner: string,
  repo: string,
  branch: string,
  path: string,
): Promise<string> {
  const data = await gh<{ content?: string; encoding?: string }>(
    `/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/contents/${path
      .split("/")
      .map(encodeURIComponent)
      .join("/")}?ref=${encodeURIComponent(branch)}`,
  );
  if (!data.content || data.encoding !== "base64") {
    throw new Error(`GitHub did not return file content for ${path}`);
  }
  const bytes = Uint8Array.from(atob(data.content.replace(/\n/g, "")), (c) => c.charCodeAt(0));
  return new TextDecoder("utf-8").decode(bytes);
}
