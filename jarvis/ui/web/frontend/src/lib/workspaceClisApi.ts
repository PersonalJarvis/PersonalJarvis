// REST client for the terminal CLIs the user added themselves.
//
// Its own module rather than a corner of `agenticIdeApi`, because these are
// writes against a small, self-contained store while that file is the read path
// for a live IDE session. What the two share is the RESULT: an entry created
// here shows up in `fetchIdeAgents()` on the next read, because the backend puts
// it in the same registry the shipped CLIs live in.

export interface CustomCli {
  /** Stable registry key. Never changes, not even on a rename. */
  id: string;
  display_name: string;
  /** The command line that starts this CLI. */
  command: string;
  description: string;
  /** `"at"` writes a dropped file as `@path`, `"quoted"` as `"path"`. */
  file_reference: "at" | "quoted";
  /** Where to fetch this entry's mark; empty when it has none. */
  logo_url: string;
  /**
   * True when the command is shell source — a pipeline, an environment
   * assignment, two commands chained — and therefore starts inside a shell
   * rather than as the pane's own process. Shown to the user because it changes
   * what "the pane exited" means.
   */
  runs_through_shell: boolean;
  /** The word that has to be on PATH for this command to start. */
  binary: string;
}

export interface CustomCliList {
  clis: CustomCli[];
  max_name_length: number;
  max_command_length: number;
  max_logo_bytes: number;
  logo_extensions: string[];
}

export interface CustomCliDraft {
  display_name: string;
  command: string;
  description?: string;
  file_reference?: "at" | "quoted";
}

const BASE = "/api/workspace-clis";

/**
 * The server's own complaint, or a generic one.
 *
 * These endpoints answer a form the user is looking at, and every validation
 * message they return is written to be read by that person ("Give the command
 * that starts this CLI"). Throwing away that text for a status code would turn
 * a fixable typo into a mystery.
 */
async function detail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (body?.detail) return body.detail;
  } catch {
    /* fall through */
  }
  return `request failed: ${res.status}`;
}

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { cache: "no-store", ...init });
  if (!res.ok) throw new Error(await detail(res));
  return (await res.json()) as T;
}

export function fetchCustomClis(): Promise<CustomCliList> {
  return send<CustomCliList>(BASE);
}

export function createCustomCli(draft: CustomCliDraft): Promise<CustomCli> {
  return send<CustomCli>(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

export function updateCustomCli(
  id: string,
  changes: Partial<CustomCliDraft>,
): Promise<CustomCli> {
  return send<CustomCli>(`${BASE}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
}

export function deleteCustomCli(id: string): Promise<{ ok: boolean }> {
  return send<{ ok: boolean }>(`${BASE}/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function uploadCustomCliLogo(
  id: string,
  file: File,
): Promise<CustomCli> {
  const body = new FormData();
  body.append("file", file);
  // No Content-Type header on purpose: the browser has to set the multipart
  // boundary itself, and naming the type by hand omits it.
  return send<CustomCli>(`${BASE}/${encodeURIComponent(id)}/logo`, {
    method: "PUT",
    body,
  });
}

export function removeCustomCliLogo(id: string): Promise<CustomCli> {
  return send<CustomCli>(`${BASE}/${encodeURIComponent(id)}/logo`, {
    method: "DELETE",
  });
}
