/**
 * Talking to `/api/skills/upload` — inspect first, install second.
 *
 * The two-call shape is deliberate. Inspecting is what lets the dialog show
 * the real skill name, the files that will actually be installed and every
 * blocker at once, *before* anything is written. Installing then repeats the
 * upload rather than referencing a staged copy, which costs a second transfer
 * over a loopback connection and buys a server that keeps no half-finished
 * state around.
 */

import type { PickedFile } from "@/lib/filePicking";

export type SkillUploadLimits = {
  max_file_bytes: number;
  max_total_bytes: number;
  max_file_count: number;
};

export type DetectedSkill = {
  name: string;
  description: string;
  category: string;
  version: string;
  tags: string[];
  /** The state the install would produce — `draft` when the lint bites. */
  state: string;
  resource_count: number;
};

export type SkillUploadReport = {
  /** True when nothing blocks the install. Lint findings do not block. */
  ready: boolean;
  problems: string[];
  lint_findings: string[];
  /** Paths as they will be installed, relative to the skill folder. */
  files: string[];
  /** OS clutter that was dropped — shown so the file count adds up. */
  ignored: string[];
  stripped_root: string | null;
  total_bytes: number;
  skill: DetectedSkill | null;
  limits: SkillUploadLimits;
};

function buildUploadBody(picked: PickedFile[]): FormData {
  const body = new FormData();
  for (const { file } of picked) body.append("files", file);
  // The relative paths travel beside the files, in the same order: a
  // multipart body has no field for them, and without them a folder drop
  // would arrive as a pile of loose files.
  body.append("paths", JSON.stringify(picked.map((entry) => entry.path)));
  return body;
}

async function postUpload<T>(url: string, picked: PickedFile[]): Promise<T> {
  const res = await fetch(url, { method: "POST", body: buildUploadBody(picked) });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

/** Reports what the upload holds. Writes nothing. */
export function inspectSkillUpload(picked: PickedFile[]): Promise<SkillUploadReport> {
  return postUpload<SkillUploadReport>("/api/skills/upload/inspect", picked);
}

/** Installs the upload and returns the skill detail. */
export function uploadSkill<T>(picked: PickedFile[]): Promise<T> {
  return postUpload<T>("/api/skills/upload", picked);
}
