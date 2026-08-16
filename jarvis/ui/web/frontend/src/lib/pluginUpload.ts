/**
 * Talking to `/api/marketplace/plugins/upload` — inspect first, install second.
 *
 * Same two-call shape as the skill flow and for the same reason: the dialog
 * has to be able to say what will happen before it happens. What differs is
 * what there is to report. A plugin is a manifest, so the interesting facts
 * are the catalog card it will produce and the authentication mode it will
 * ask the owner for.
 */

import type { PickedFile } from "@/lib/filePicking";

export type DetectedPlugin = {
  id: string;
  display_name: string;
  description: string;
  category: string;
  /** Which of the catalog auth modes the plugin will ask for. */
  auth_mode: string;
  longevity: string;
};

export type PluginUploadReport = {
  ready: boolean;
  problems: string[];
  plugin: DetectedPlugin | null;
  /** True when an mcp.json sat beside the manifest and travels with it. */
  has_mcp: boolean;
  files: string[];
  ignored: string[];
  stripped_root: string | null;
  total_bytes: number;
  limits: { max_file_bytes: number; max_total_bytes: number; max_file_count: number };
};

export type PluginUploadResult = {
  ok: boolean;
  plugin: { id: string; display_name: string; source: string };
};

function buildUploadBody(picked: PickedFile[]): FormData {
  const body = new FormData();
  for (const { file } of picked) body.append("files", file);
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
export function inspectPluginUpload(picked: PickedFile[]): Promise<PluginUploadReport> {
  return postUpload<PluginUploadReport>(
    "/api/marketplace/plugins/upload/inspect",
    picked,
  );
}

/** Installs the manifest into the local catalog. */
export function uploadPlugin(picked: PickedFile[]): Promise<PluginUploadResult> {
  return postUpload<PluginUploadResult>("/api/marketplace/plugins/upload", picked);
}
