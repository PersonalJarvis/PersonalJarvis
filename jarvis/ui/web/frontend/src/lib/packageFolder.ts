/**
 * Read a dropped or picked plugin/skill folder into a publish draft.
 *
 * "The folder is the classification" (docs/marketplace/publishing-plan.md §2):
 * the author never declares what kind of package they are uploading — what is
 * inside the directory decides it. A folder with a plugin.json becomes a
 * plugin draft (with mcp.json, bundled skills and the usage card picked up
 * alongside); a folder with only a SKILL.md becomes a skill draft.
 *
 * Everything here is client-side file reading — nothing is uploaded until the
 * user presses Publish, so an author can inspect the prefilled form first.
 */

export interface PackageSkill {
  name: string;
  skill_md: string;
}

export interface FolderDraft {
  kind: "skill" | "plugin";
  name: string;
  version: string;
  title: string;
  description: string;
  categories: string;
  skill_md: string;
  plugin_json_text: string;
  mcp_json_text: string;
  usage_card: string;
  skills: PackageSkill[];
  /** Non-fatal notes shown to the author ("mcp.json not found", …). */
  warnings: string[];
  /** Relative paths that were recognized, for the file chip list. */
  files: string[];
}

interface NamedFile {
  /** Path relative to the dropped root, forward slashes. */
  path: string;
  file: File;
}

const MAX_READ_BYTES = 512 * 1024;

/** Mirrors the store's per-SKILL.md cap (jarvis/marketplace/publish.py
 *  MAX_SKILL_BYTES) — used to show the size live, before the Check button
 *  round-trips to the real validator. */
export const MAX_SKILL_MD_BYTES = 64 * 1024;

/** Files worth reading at all — everything else in the folder is ignored. */
const INTERESTING = /(^|\/)(plugin\.json|mcp\.json|SKILL\.md|usage-card\.md)$/;

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.readAsText(file);
  });
}

/** Minimal YAML frontmatter reader: top-level `key: value` string pairs. */
export function frontmatterValues(skillMd: string): Record<string, string> {
  const out: Record<string, string> = {};
  if (!skillMd.startsWith("---")) return out;
  const rest = skillMd.slice(3);
  const end = rest.indexOf("\n---");
  if (end === -1) return out;
  for (const line of rest.slice(0, end).split("\n")) {
    const m = /^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$/.exec(line);
    if (!m) continue;
    let value = m[2].trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    out[m[1]] = value;
  }
  return out;
}

/**
 * Server names in an mcp.json document whose entry has no "type" key.
 *
 * A pre-upload nudge only — the real rule (every server MUST declare
 * "type": "streamable-http" | "stdio" | "sse") lives in publish.py, which
 * now delegates it to agent_plugins_loader.validate_mcp_server, the same
 * function the install path uses. Best-effort JSON parse: invalid JSON is
 * already reported by the plugin.json/mcp.json field errors this function
 * does not duplicate, so it silently yields no warning here instead.
 */
export function mcpServersMissingType(mcpJsonText: string): string[] {
  try {
    const parsed = JSON.parse(mcpJsonText) as { mcpServers?: Record<string, { type?: unknown }> };
    const servers = parsed.mcpServers;
    if (!servers || typeof servers !== "object") return [];
    return Object.entries(servers)
      .filter(([, server]) => !server || typeof server !== "object" || typeof server.type !== "string")
      .map(([name]) => name);
  } catch {
    return [];
  }
}

/** Recursively collect files from a drag-and-drop DataTransfer. */
export async function collectDroppedFiles(items: DataTransferItemList): Promise<NamedFile[]> {
  const roots: FileSystemEntry[] = [];
  for (const item of Array.from(items)) {
    const entry = item.webkitGetAsEntry?.();
    if (entry) roots.push(entry);
  }
  const found: NamedFile[] = [];

  async function walk(entry: FileSystemEntry, prefix: string): Promise<void> {
    if (found.length > 400) return; // runaway folder guard
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) =>
        (entry as FileSystemFileEntry).file(resolve, reject),
      );
      found.push({ path: prefix + entry.name, file });
      return;
    }
    if (entry.isDirectory) {
      const dirReader = (entry as FileSystemDirectoryEntry).createReader();
      // readEntries returns results in batches; loop until empty.
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
          dirReader.readEntries(resolve, reject),
        );
        if (batch.length === 0) break;
        for (const child of batch) await walk(child, prefix + entry.name + "/");
      }
    }
  }

  for (const root of roots) await walk(root, "");
  return found;
}

/** Collect files from an `<input webkitdirectory>` selection. */
export function collectPickedFiles(list: FileList): NamedFile[] {
  return Array.from(list).map((file) => ({
    path: (file.webkitRelativePath || file.name).replace(/\\/g, "/"),
    file,
  }));
}

/**
 * Strip the common leading directory ("my-plugin/…" → "…") so detection works
 * the same whether the user dropped the folder itself or its contents.
 */
function normalizePaths(files: NamedFile[]): NamedFile[] {
  if (files.length === 0) return files;
  const first = files[0].path;
  const slash = first.indexOf("/");
  if (slash === -1) return files;
  const prefix = first.slice(0, slash + 1);
  if (files.every((f) => f.path.startsWith(prefix))) {
    return files.map((f) => ({ ...f, file: f.file, path: f.path.slice(prefix.length) }));
  }
  return files;
}

/** Turn the recognized files of a package directory into a prefilled draft. */
export async function folderToDraft(input: NamedFile[]): Promise<FolderDraft> {
  const files = normalizePaths(input).filter(
    (f) => INTERESTING.test(f.path) && f.file.size <= MAX_READ_BYTES,
  );
  const warnings: string[] = [];
  const used: string[] = [];

  const byPath = (p: string) => files.find((f) => f.path === p);
  const read = async (f: NamedFile | undefined): Promise<string> => {
    if (!f) return "";
    used.push(f.path);
    return (await readText(f.file)).replace(/^﻿/, "");
  };

  const draft: FolderDraft = {
    kind: "skill",
    name: "",
    version: "1.0.0",
    title: "",
    description: "",
    categories: "",
    skill_md: "",
    plugin_json_text: "",
    mcp_json_text: "",
    usage_card: "",
    skills: [],
    warnings,
    files: used,
  };

  const pluginJsonFile = byPath("plugin.json");
  if (pluginJsonFile) {
    // ---- Plugin package -------------------------------------------------
    draft.kind = "plugin";
    draft.plugin_json_text = await read(pluginJsonFile);
    try {
      const manifest = JSON.parse(draft.plugin_json_text) as Record<string, unknown>;
      if (typeof manifest.name === "string") draft.name = manifest.name;
      if (typeof manifest.version === "string") draft.version = manifest.version;
      if (typeof manifest.description === "string") draft.description = manifest.description;
    } catch {
      warnings.push("plugin.json is not valid JSON — fix it before publishing.");
    }
    draft.mcp_json_text = await read(byPath("mcp.json"));
    draft.usage_card = await read(byPath("io.github.personaljarvis/usage-card.md"));
    if (!draft.mcp_json_text) {
      warnings.push(
        "No mcp.json found — fine when the auth block points at a hosted MCP server or the package is skills-only.",
      );
    } else {
      const missingType = mcpServersMissingType(draft.mcp_json_text);
      if (missingType.length > 0) {
        warnings.push(
          `mcp.json server${missingType.length === 1 ? "" : "s"} ` +
            `${missingType.map((n) => `"${n}"`).join(", ")} missing "type" — the store requires ` +
            `"type": "streamable-http" | "stdio" | "sse" on every server.`,
        );
      }
    }
    for (const f of files) {
      const m = /^skills\/([^/]+)\/SKILL\.md$/.exec(f.path);
      if (!m) continue;
      if (draft.skills.length >= 10) {
        warnings.push("More than 10 bundled skills — the store accepts at most 10.");
        break;
      }
      draft.skills.push({ name: m[1], skill_md: await read(f) });
    }
    return draft;
  }

  // ---- Standalone skill ------------------------------------------------
  const skillFile =
    byPath("SKILL.md") ?? files.find((f) => f.path.endsWith("/SKILL.md")) ?? undefined;
  if (!skillFile) {
    throw new Error(
      "No plugin.json and no SKILL.md found — a package folder needs one of the two at its top level.",
    );
  }
  draft.skill_md = await read(skillFile);
  const fm = frontmatterValues(draft.skill_md);
  const dirName = skillFile.path.includes("/") ? skillFile.path.split("/")[0] : "";
  draft.name = fm.name || dirName;
  if (fm.version) draft.version = fm.version;
  draft.description = fm.description ?? "";
  draft.title =
    fm.title ||
    (draft.name ? draft.name.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "");
  if (fm.category) draft.categories = fm.category;
  if (!draft.skill_md.startsWith("---")) {
    warnings.push("SKILL.md does not start with YAML frontmatter (---) — the store requires it.");
  }
  return draft;
}
