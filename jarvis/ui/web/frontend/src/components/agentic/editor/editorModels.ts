import type * as Monaco from "monaco-editor/editor/editor.api";

import { useEventStore } from "@/store/events";
import { useCodeEditorStore, type EditorFile } from "@/store/codeEditor";
import {
  SaveConflictError,
  fetchFileVersion,
  fetchHeadText,
  loadTextFile,
  saveTextFile,
  type LineEnding,
} from "./editorApi";

/**
 * The open files' text, held in the editor engine's models.
 *
 * Only a TYPE import of the engine lives here, so this module stays in the main
 * chunk and the multi-megabyte engine loads lazily with the editor surface,
 * which hands it over through {@link setMonaco}. Until then nothing here can
 * hold text, and nothing needs to: no file loads before the surface mounts.
 *
 * "Dirty" is computed the way desktop editors do it — the model's alternative
 * version id against the one recorded at the last load or save — so undoing
 * back to the saved text clears the dot again.
 */
type MonacoApi = typeof Monaco;

interface Entry {
  model: Monaco.editor.ITextModel;
  /** Alternative version id at the last load or save; -1 = never saved as is. */
  saved: number;
  listener: Monaco.IDisposable;
}

let api: MonacoApi | null = null;
const entries = new Map<string, Entry>();
const heads = new Map<string, Monaco.editor.ITextModel>();
const viewStates = new Map<string, Monaco.editor.ICodeEditorViewState | null>();
const headLoads = new Map<string, Promise<Monaco.editor.ITextModel | null>>();
/**
 * Bumped when a save starts and when it ends. A disk check that began before
 * a save may come back with the version the save just replaced; comparing the
 * epoch before and after the request drops such a stale answer.
 */
const saveEpochs = new Map<string, number>();
const bumpEpoch = (fileKey: string) => saveEpochs.set(fileKey, (saveEpochs.get(fileKey) ?? 0) + 1);

export function setMonaco(monaco: MonacoApi): void {
  api = monaco;
}

export function monacoApi(): MonacoApi | null {
  return api;
}

const store = () => useCodeEditorStore.getState();

const IMAGE_FILE = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i;

export const isImagePath = (path: string) => IMAGE_FILE.test(path);

/** The engine's language id for a file name, `plaintext` when nothing claims it. */
export function languageFor(path: string): string {
  if (!api) return "plaintext";
  const name = (path.split("/").pop() ?? path).toLowerCase();
  const dot = name.lastIndexOf(".");
  const extension = dot > 0 ? name.slice(dot) : "";
  const languages = api.languages.getLanguages();
  const byName = languages.find((language) => language.filenames?.some((entry) => entry.toLowerCase() === name));
  if (byName) return byName.id;
  const byExtension =
    extension && languages.find((language) => language.extensions?.some((entry) => entry.toLowerCase() === extension));
  return byExtension ? byExtension.id : "plaintext";
}

/** A language id as people say it ("Python", "TypeScript"). */
export function languageName(id: string): string {
  const language = api?.languages.getLanguages().find((entry) => entry.id === id);
  return language?.aliases?.[0] ?? id;
}

function uriFor(workspaceId: string, path: string, scheme = "file"): Monaco.Uri {
  return api!.Uri.from({ scheme, path: `/${encodeURIComponent(workspaceId)}/${path}` });
}

function eolSequence(eol: LineEnding): Monaco.editor.EndOfLineSequence {
  return eol === "\r\n" ? api!.editor.EndOfLineSequence.CRLF : api!.editor.EndOfLineSequence.LF;
}

function syncDirty(fileKey: string): void {
  const entry = entries.get(fileKey);
  const file = store().files[fileKey];
  if (!entry || !file) return;
  const dirty = entry.model.getAlternativeVersionId() !== entry.saved;
  if (dirty !== file.dirty) store().patchFile(fileKey, { dirty });
}

function createEntry(
  fileKey: string,
  file: Pick<EditorFile, "workspaceId" | "path">,
  text: string,
  eol: LineEnding,
  saved: boolean,
): Entry {
  const model = api!.editor.createModel(text, languageFor(file.path), uriFor(file.workspaceId, file.path));
  // Text without a line break says nothing about its line ending; set it
  // before the saved marker is taken so a fresh load is never dirty.
  model.setEOL(eolSequence(eol));
  const entry: Entry = {
    model,
    saved: saved ? model.getAlternativeVersionId() : -1,
    listener: model.onDidChangeContent(() => syncDirty(fileKey)),
  };
  entries.set(fileKey, entry);
  return entry;
}

export function modelOf(fileKey: string): Monaco.editor.ITextModel | null {
  return entries.get(fileKey)?.model ?? null;
}

export function rememberViewState(tabKey: string, state: Monaco.editor.ICodeEditorViewState | null): void {
  viewStates.set(tabKey, state);
}

export function viewStateOf(tabKey: string): Monaco.editor.ICodeEditorViewState | null {
  return viewStates.get(tabKey) ?? null;
}

function disposeFile(fileKey: string): void {
  const entry = entries.get(fileKey);
  if (entry) {
    entry.listener.dispose();
    entry.model.dispose();
    entries.delete(fileKey);
  }
  heads.get(fileKey)?.dispose();
  heads.delete(fileKey);
  headLoads.delete(fileKey);
  saveEpochs.delete(fileKey);
  for (const key of [...viewStates.keys()]) if (key.endsWith(`:${fileKey}`)) viewStates.delete(key);
}

// A file record leaving the store (its last tab closed) frees its models.
useCodeEditorStore.subscribe((state, previous) => {
  for (const key of Object.keys(previous.files)) if (!state.files[key]) disposeFile(key);
});

/** Put text read from disk into a buffer, keeping undo, and call it saved. */
function replaceFromDisk(entry: Entry, text: string, eol: LineEnding): void {
  const { model } = entry;
  if (model.getValue() !== text) {
    model.pushEOL(eolSequence(eol));
    model.pushEditOperations([], [{ range: model.getFullModelRange(), text }], () => null);
    model.pushStackElement();
  }
  entry.saved = model.getAlternativeVersionId();
}

function toast(message: string): void {
  useEventStore.getState().pushToast("error", message);
}

/** Load a file's text from disk into its model (or record why it cannot be edited). */
export async function loadFile(fileKey: string): Promise<void> {
  const file = store().files[fileKey];
  if (!file || !api) return;
  if (isImagePath(file.path)) {
    store().patchFile(fileKey, { status: "image" });
    return;
  }
  try {
    const loaded = await loadTextFile(file.workspaceId, file.path);
    if (!store().files[fileKey]) return;
    if (loaded.text === null) {
      store().patchFile(fileKey, { status: loaded.too_large ? "too_large" : "binary", version: loaded.version });
      return;
    }
    const existing = entries.get(fileKey);
    if (existing) replaceFromDisk(existing, loaded.text, loaded.eol);
    else createEntry(fileKey, file, loaded.text, loaded.eol, true);
    store().patchFile(fileKey, {
      status: "ready",
      error: "",
      version: loaded.version,
      encoding: loaded.encoding,
      eol: loaded.eol,
      deleted: false,
      conflict: null,
      dirty: false,
    });
  } catch (error) {
    if (!store().files[fileKey]) return;
    // A file deleted on disk (a deleted entry in Changes) opens as an empty
    // buffer marked deleted: its diff shows what was removed, and a save
    // brings it back.
    const missing = await fetchFileVersion(file.workspaceId, file.path).then(
      (version) => version === null,
      () => false,
    );
    if (missing && store().files[fileKey] && !entries.has(fileKey)) {
      createEntry(fileKey, file, "", "\n", true);
      store().patchFile(fileKey, { status: "ready", error: "", version: null, deleted: true, dirty: false });
      return;
    }
    if (store().files[fileKey]) store().patchFile(fileKey, { status: "error", error: (error as Error).message });
  }
}

/** The committed text a diff tab compares against; empty for a new file. */
export function loadHead(fileKey: string): Promise<Monaco.editor.ITextModel | null> {
  // One request per file at a time: two diff tabs switching back and forth
  // must not both create the same model. Each new showing re-reads the commit,
  // so a commit made meanwhile shows up.
  const pending = headLoads.get(fileKey);
  if (pending) return pending;
  const load = (async () => {
    const file = store().files[fileKey];
    if (!file || !api) return null;
    let text: string | null = null;
    try {
      text = await fetchHeadText(file.workspaceId, file.path);
    } catch (error) {
      toast((error as Error).message);
    }
    if (!store().files[fileKey]) return null;
    const existing = heads.get(fileKey);
    if (existing) {
      if (existing.getValue() !== (text ?? "")) existing.setValue(text ?? "");
      return existing;
    }
    const model = api.editor.createModel(text ?? "", languageFor(file.path), uriFor(file.workspaceId, file.path, "jarvis-head"));
    heads.set(fileKey, model);
    return model;
  })().finally(() => headLoads.delete(fileKey));
  headLoads.set(fileKey, load);
  return load;
}

/**
 * Write a buffer to disk.
 *
 * `overwrite` is the conflict bar's "Keep mine": it saves over whatever is on
 * disk now. Without it a save names the version it was based on, and a file an
 * agent changed meanwhile turns into a conflict instead of being overwritten.
 */
export async function saveFile(fileKey: string, { overwrite = false } = {}): Promise<boolean> {
  const file = store().files[fileKey];
  const entry = entries.get(fileKey);
  if (!file || !entry || file.saving) return false;
  const expected = overwrite ? (file.conflict?.diskVersion ?? null) : file.version;
  const create = overwrite ? file.conflict?.diskVersion == null : file.deleted || file.version === null;
  const sent = entry.model.getAlternativeVersionId();
  bumpEpoch(fileKey);
  store().patchFile(fileKey, { saving: true });
  try {
    const saved = await saveTextFile(file.workspaceId, {
      path: file.path,
      text: entry.model.getValue(),
      expectedVersion: expected,
      encoding: file.encoding,
      create,
    });
    entry.saved = sent;
    bumpEpoch(fileKey);
    store().patchFile(fileKey, {
      saving: false,
      version: saved.version,
      eol: saved.eol,
      deleted: false,
      conflict: null,
      dirty: entry.model.getAlternativeVersionId() !== sent,
    });
    return true;
  } catch (error) {
    bumpEpoch(fileKey);
    if (error instanceof SaveConflictError) {
      store().patchFile(fileKey, { saving: false, conflict: { diskVersion: error.currentVersion } });
    } else {
      store().patchFile(fileKey, { saving: false });
      toast((error as Error).message);
    }
    return false;
  }
}

export async function saveAll(workspaceId: string): Promise<void> {
  const dirty = Object.entries(store().files).filter(([, file]) => file.workspaceId === workspaceId && file.dirty);
  for (const [key] of dirty) await saveFile(key);
}

/**
 * Throw the buffer away and show what is on disk now.
 *
 * `follow` is the quiet reload of a clean buffer after an agent's edit: if the
 * user starts typing while the file is being read, their edit wins and the
 * disk version turns into a conflict instead of replacing what they typed.
 */
export async function revertFile(fileKey: string, { follow = false } = {}): Promise<void> {
  const file = store().files[fileKey];
  const entry = entries.get(fileKey);
  if (!file || !entry) return;
  const before = entry.model.getAlternativeVersionId();
  try {
    const loaded = await loadTextFile(file.workspaceId, file.path);
    if (entries.get(fileKey) !== entry) return;
    if (loaded.text === null) {
      toast("The file on disk can no longer be edited here.");
      return;
    }
    if (follow && entry.model.getAlternativeVersionId() !== before) {
      store().patchFile(fileKey, { conflict: { diskVersion: loaded.version } });
      return;
    }
    replaceFromDisk(entry, loaded.text, loaded.eol);
    store().patchFile(fileKey, {
      version: loaded.version,
      encoding: loaded.encoding,
      eol: loaded.eol,
      deleted: false,
      conflict: null,
      dirty: false,
    });
  } catch (error) {
    toast((error as Error).message);
  }
}

/** Forget unsaved edits without touching disk (closing with "Don't save"). */
export function discardFile(fileKey: string): void {
  disposeFile(fileKey);
  if (store().files[fileKey]) store().patchFile(fileKey, { dirty: false, status: "loading" });
}

/**
 * Notice an agent's edit to an open file.
 *
 * A clean buffer quietly follows the disk, keeping its undo history and the
 * view where it was; a buffer with unsaved edits gets a conflict bar instead.
 */
export async function checkDisk(fileKey: string): Promise<void> {
  const before = store().files[fileKey];
  if (!before || before.saving || before.status === "loading" || before.status === "error") return;
  const epoch = saveEpochs.get(fileKey) ?? 0;
  let version: string | null;
  try {
    version = await fetchFileVersion(before.workspaceId, before.path);
  } catch {
    return; // the next check asks again; a failed poll changes nothing
  }
  const file = store().files[fileKey];
  // A save started or finished meanwhile: this answer may predate it.
  if (!file || file.saving || (saveEpochs.get(fileKey) ?? 0) !== epoch) return;
  if (version === null) {
    if (!file.deleted) store().patchFile(fileKey, { deleted: true });
    return;
  }
  if (version === file.version && !file.deleted) return;
  if (file.status !== "ready") {
    await loadFile(fileKey);
    return;
  }
  if (!file.dirty) await revertFile(fileKey, { follow: true });
  else if (file.conflict?.diskVersion !== version) store().patchFile(fileKey, { conflict: { diskVersion: version } });
}

/**
 * Carry open buffers along with an explorer rename, before the store moves
 * the tabs: a moved file keeps its unsaved edits under its new name.
 */
export function moveModels(workspaceId: string, from: string, to: string): void {
  if (!api) return;
  const prefix = `${workspaceId}\u0000`;
  for (const [fileKey, entry] of [...entries]) {
    if (!fileKey.startsWith(prefix)) continue;
    const path = fileKey.slice(prefix.length);
    if (path !== from && !path.startsWith(`${from}/`)) continue;
    const moved = to + path.slice(from.length);
    const text = entry.model.getValue();
    const eol = entry.model.getEOL() === "\r\n" ? "\r\n" : "\n";
    const dirty = entry.model.getAlternativeVersionId() !== entry.saved;
    disposeFile(fileKey);
    createEntry(`${prefix}${moved}`, { workspaceId, path: moved }, text, eol, !dirty);
  }
}
