import { Suspense, lazy, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useShallow } from "zustand/react/shallow";
import {
  AlertTriangle,
  ChevronRight,
  Code2,
  Copy,
  Eye,
  ExternalLink,
  FileDiff,
  FileText,
  FolderSearch,
  Loader2,
  SquareArrowOutUpRight,
  SquareTerminal,
  X,
} from "lucide-react";

import { fill, useT } from "@/i18n";
import { attachToTerminal, openTerminalTarget } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { revealInExplorer } from "@/store/ideExplorer";
import { tabsOf, useCodeEditorStore, type EditorTab } from "@/store/codeEditor";
import { FileTypeIcon } from "@/components/agentic/sidePanel/explorer/FileTypeIcon";
import { absoluteWorkspacePath } from "@/components/agentic/sidePanel/explorer/explorerApi";
import { ThreadMenuItem, ThreadMenuSeparator, ThreadPopover } from "@/components/agentic/threads/ThreadPopover";
import { checkDisk, discardFile, languageName, revertFile, saveAll, saveFile } from "./editorModels";
import { QuickOpen } from "./QuickOpen";
import { renderKindOf } from "./fileKinds";

const EditorSurface = lazy(() => import("./EditorSurface"));

/** How often the open file is compared with the disk while the editor is on screen. */
const DISK_POLL_MS = 3000;
const DISK_JITTER_MS = 1500;

const baseName = (path: string) => path.split("/").pop() ?? path;

interface PendingClose {
  keys: string[];
  fileKeys: string[];
}

function copyText(text: string): void {
  void navigator.clipboard?.writeText(text).catch((error: unknown) =>
    useEventStore.getState().pushToast("error", (error as Error).message),
  );
}

/**
 * The code editor, laid over the terminal grid of the Agentic IDE.
 *
 * Files open here from the side panel's Folder and Changes tabs, from a
 * Ctrl+click on a path in a terminal, and from Go to File (Ctrl+P). The
 * terminals stay mounted underneath; "Terminals" in the tab bar brings them
 * back without closing a tab.
 */
export function CodeEditorStage({
  workspaceId,
  workspacePath,
  stagedPane,
}: {
  workspaceId: string | null;
  workspacePath: string;
  stagedPane: string | null;
}) {
  const t = useT();
  const stage = useRef<HTMLDivElement>(null);
  const tabs = useCodeEditorStore(useShallow((state) => tabsOf(state, workspaceId)));
  const activeKey = useCodeEditorStore((state) => (workspaceId ? (state.active[workspaceId] ?? null) : null));
  const visible = useCodeEditorStore((state) => state.visible);
  const quickOpen = useCodeEditorStore((state) => state.quickOpen);
  const files = useCodeEditorStore((state) => state.files);
  const [pending, setPending] = useState<PendingClose | null>(null);
  const [menu, setMenu] = useState<EditorTab | null>(null);
  // Tabs whose Markdown, HTML or SVG is shown rendered (Ctrl+Shift+V).
  const [rendered, setRendered] = useState<ReadonlySet<string>>(() => new Set());
  const toggleRendered = (key: string) =>
    setRendered((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const toggleRenderedRef = useRef(toggleRendered);
  toggleRenderedRef.current = toggleRendered;
  const menuAnchor = useRef<HTMLElement | null>(null);
  const pushToast = useEventStore((state) => state.pushToast);

  const activeTab = tabs.find((tab) => tab.key === activeKey) ?? tabs[tabs.length - 1] ?? null;
  const activeFile = activeTab ? files[activeTab.fileKey] : undefined;
  const shown = visible && activeTab !== null;

  // Closing the last tab hands the stage back to the terminals.
  useEffect(() => {
    if (visible && workspaceId && tabs.length === 0) useCodeEditorStore.getState().setVisible(false);
  }, [visible, workspaceId, tabs.length]);

  /** Close tabs, asking first when that would throw away unsaved edits. */
  const requestClose = (keys: string[]) => {
    const state = useCodeEditorStore.getState();
    const closing = new Set(keys);
    const remaining = new Set(state.tabs.filter((tab) => !closing.has(tab.key)).map((tab) => tab.fileKey));
    const fileKeys = [
      ...new Set(
        state.tabs
          .filter((tab) => closing.has(tab.key) && !remaining.has(tab.fileKey) && state.files[tab.fileKey]?.dirty)
          .map((tab) => tab.fileKey),
      ),
    ];
    if (fileKeys.length === 0) state.closeTabs(keys);
    else setPending({ keys, fileKeys });
  };

  const requestCloseRef = useRef(requestClose);
  requestCloseRef.current = requestClose;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!workspaceId || !(event.ctrlKey || event.metaKey) || event.altKey) return;
      if (useEventStore.getState().activeSection !== "agentic-ide") return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      // A terminal keeps every Ctrl key for its shell (Ctrl+P, Ctrl+W, …).
      if (target?.closest(".xterm")) return;
      const key = event.key.toLowerCase();
      const state = useCodeEditorStore.getState();
      if (key === "p" && !event.shiftKey) {
        // A text field outside the editor (a rename box, the chat composer)
        // keeps its keys; the editor's own text area does not count as one.
        const field = target?.closest("input, textarea, [contenteditable='true']");
        if (field && !stage.current?.contains(field)) return;
        event.preventDefault();
        event.stopPropagation();
        state.setQuickOpen(true);
        return;
      }
      const host = stage.current;
      if (!state.visible || !host) return;
      if (target && target !== document.body && !host.contains(target)) return;
      const own = tabsOf(state, workspaceId);
      const current = own.find((tab) => tab.key === state.active[workspaceId]) ?? own[own.length - 1];
      if (!current) return;
      const handled = () => {
        event.preventDefault();
        event.stopPropagation();
      };
      if (key === "s") {
        handled();
        if (event.shiftKey) void saveAll(workspaceId);
        else void saveFile(current.fileKey);
      } else if (key === "w" && !event.shiftKey) {
        handled();
        requestCloseRef.current([current.key]);
      } else if (key === "tab" || key === "pagedown" || key === "pageup") {
        handled();
        state.cycle(workspaceId, event.shiftKey || key === "pageup" ? -1 : 1);
      } else if (key === "t" && event.shiftKey) {
        handled();
        state.reopenClosed(workspaceId);
      } else if (key === "v" && event.shiftKey && current.mode === "edit" && renderKindOf(current.path)) {
        handled();
        toggleRenderedRef.current(current.key);
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [workspaceId]);

  // Notice an agent's edit to the open file: on a timer and when the window regains focus.
  const activeFileKey = shown ? activeTab.fileKey : null;
  useEffect(() => {
    if (!activeFileKey) return;
    let alive = true;
    let timer: number | undefined;
    const check = () => {
      if (document.visibilityState === "visible" && useEventStore.getState().activeSection === "agentic-ide") {
        void checkDisk(activeFileKey);
      }
    };
    const tick = () => {
      check();
      if (alive) timer = window.setTimeout(tick, DISK_POLL_MS + Math.random() * DISK_JITTER_MS);
    };
    timer = window.setTimeout(tick, DISK_POLL_MS);
    window.addEventListener("focus", check);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      window.removeEventListener("focus", check);
    };
  }, [activeFileKey]);

  const duplicateNames = useMemo(() => {
    // Two different files with one name get their folder beside it; the edit
    // and diff tab of one file do not.
    const counts = new Map<string, number>();
    for (const path of new Set(tabs.map((tab) => tab.path))) counts.set(baseName(path), (counts.get(baseName(path)) ?? 0) + 1);
    return counts;
  }, [tabs]);

  if (!workspaceId) return null;

  const openExternally = (path: string) =>
    void openTerminalTarget(workspaceId, path).catch((error: unknown) => pushToast("error", (error as Error).message));
  const absolute = (path: string) => absoluteWorkspacePath(workspacePath, path);

  const quick = quickOpen ? <QuickOpen workspaceId={workspaceId} /> : null;
  if (!shown) return quick;

  const store = useCodeEditorStore.getState();
  const segments = activeTab.path.split("/");

  return (
    <>
      <div
        ref={stage}
        data-testid="code-editor"
        data-path={activeTab.path}
        className="absolute inset-0 z-20 flex min-h-0 flex-col bg-background"
      >
        {/* Tab bar */}
        <div className="flex h-9 shrink-0 items-stretch border-b border-border/70 bg-sidebar">
          <div role="tablist" aria-label={t("code_editor.tabs")} className="scrollbar-none flex min-w-0 flex-1 items-stretch overflow-x-auto">
            {tabs.map((tab) => {
              const file = files[tab.fileKey];
              const active = tab.key === activeTab.key;
              const name = baseName(tab.path);
              const folder = tab.path.split("/").slice(-2, -1)[0];
              return (
                <div
                  key={tab.key}
                  role="tab"
                  aria-selected={active}
                  data-testid="code-editor-tab"
                  data-path={tab.path}
                  data-preview={tab.preview || undefined}
                  data-dirty={file?.dirty || undefined}
                  tabIndex={0}
                  title={tab.path}
                  onClick={() => store.activate(tab.key)}
                  onDoubleClick={() => store.pinTab(tab.key)}
                  onAuxClick={(event) => {
                    if (event.button === 1) {
                      event.preventDefault();
                      requestClose([tab.key]);
                    }
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") store.activate(tab.key);
                  }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    menuAnchor.current = event.currentTarget;
                    setMenu(tab);
                  }}
                  className={cn(
                    "group relative flex min-w-0 max-w-[220px] shrink-0 cursor-pointer items-center gap-1.5 border-r border-border/50 pl-3 pr-1.5 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                    active ? "bg-background text-foreground-strong" : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                  )}
                >
                  {active && <span className="absolute inset-x-0 top-0 h-px bg-primary" aria-hidden />}
                  {tab.mode === "diff" ? <FileDiff className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden /> : <FileTypeIcon path={tab.path} />}
                  <span className={cn("truncate", tab.preview && "italic", file?.deleted && "line-through decoration-destructive/70")}>{name}</span>
                  {(duplicateNames.get(name) ?? 0) > 1 && folder && <span className="shrink-0 truncate text-[11px] text-muted-foreground">{folder}</span>}
                  <button
                    type="button"
                    aria-label={fill(t("code_editor.close_tab"), { file: name })}
                    title={t("code_editor.close_tab_hint")}
                    onClick={(event) => {
                      event.stopPropagation();
                      requestClose([tab.key]);
                    }}
                    className="group/close relative inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {file?.dirty ? (
                      <>
                        <span className="h-2 w-2 rounded-full bg-foreground/70 group-hover/close:hidden" aria-hidden />
                        <X className="hidden h-3.5 w-3.5 group-hover/close:block" aria-hidden />
                      </>
                    ) : (
                      <X className={cn("h-3.5 w-3.5", !active && "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100")} aria-hidden />
                    )}
                  </button>
                </div>
              );
            })}
          </div>
          <button
            type="button"
            data-testid="code-editor-hide"
            onClick={() => store.setVisible(false)}
            title={t("code_editor.show_terminals_hint")}
            className="inline-flex shrink-0 items-center gap-1.5 border-l border-border/60 px-3 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <SquareTerminal className="h-3.5 w-3.5" aria-hidden />
            {t("code_editor.show_terminals")}
          </button>
        </div>

        {/* Breadcrumbs and file actions */}
        <div className="flex h-8 shrink-0 items-center gap-2 border-b border-border/50 px-3 text-xs text-muted-foreground">
          <nav aria-label={t("code_editor.breadcrumbs")} className="flex min-w-0 flex-1 items-center gap-0.5 overflow-hidden">
            {segments.map((segment, index) => (
              <span key={`${segment}-${index}`} className="flex min-w-0 shrink items-center gap-0.5">
                {index > 0 && <ChevronRight className="h-3 w-3 shrink-0 opacity-60" aria-hidden />}
                <span className={cn("truncate", index === segments.length - 1 && "text-foreground")}>{segment}</span>
              </span>
            ))}
            {activeTab.mode === "diff" && <span className="ml-1.5 shrink-0 rounded bg-secondary px-1.5 py-px text-[11px]">{t("code_editor.diff_badge")}</span>}
          </nav>
          {activeTab.mode === "edit" && activeFile?.status === "ready" && renderKindOf(activeTab.path) && (
            <ActionButton
              onClick={() => toggleRendered(activeTab.key)}
              icon={rendered.has(activeTab.key) ? <Code2 className="h-3.5 w-3.5" aria-hidden /> : <Eye className="h-3.5 w-3.5" aria-hidden />}
              label={rendered.has(activeTab.key) ? t("code_editor.show_source") : t("code_editor.preview")}
              hint="Ctrl+Shift+V"
              wide
            />
          )}
          {/* A text comparison only means something for a text file. */}
          {(activeTab.mode === "diff" || activeFile?.status === "ready") && (
            <ActionButton
              onClick={() => store.openFile(workspaceId, activeTab.path, { mode: activeTab.mode === "diff" ? "edit" : "diff", preview: false })}
              icon={activeTab.mode === "diff" ? <FileText className="h-3.5 w-3.5" aria-hidden /> : <FileDiff className="h-3.5 w-3.5" aria-hidden />}
              label={activeTab.mode === "diff" ? t("code_editor.open_file") : t("code_editor.open_changes")}
            />
          )}
          {stagedPane && (
            <ActionButton
              onClick={() =>
                void attachToTerminal(stagedPane, { paths: [absolute(activeTab.path)] }).catch((error: unknown) =>
                  pushToast("error", (error as Error).message),
                )
              }
              icon={<SquareArrowOutUpRight className="h-3.5 w-3.5" aria-hidden />}
              label={fill(t("code_editor.reference_in"), { pane: stagedPane })}
            />
          )}
          <ActionButton
            onClick={() => openExternally(activeTab.path)}
            icon={<ExternalLink className="h-3.5 w-3.5" aria-hidden />}
            label={t("code_editor.open_externally")}
            iconOnly
          />
        </div>

        {activeFile?.conflict && (
          <Banner tone="warning" testId="code-editor-conflict" text={t("code_editor.conflict")}>
            <BannerButton onClick={() => void revertFile(activeTab.fileKey)}>{t("code_editor.conflict_load_disk")}</BannerButton>
            <BannerButton onClick={() => void saveFile(activeTab.fileKey, { overwrite: true })}>{t("code_editor.conflict_keep_mine")}</BannerButton>
          </Banner>
        )}
        {activeFile?.deleted && !activeFile.conflict && (
          <Banner tone="muted" testId="code-editor-deleted" text={t("code_editor.deleted")} />
        )}

        <div className="relative min-h-0 flex-1">
          <Suspense
            fallback={
              <p className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                {t("code_editor.starting")}
              </p>
            }
          >
            <EditorSurface tab={activeTab} rendered={rendered.has(activeTab.key)} onOpenExternally={openExternally} />
          </Suspense>
        </div>

        <StatusBar fileKey={activeTab.fileKey} />
      </div>

      <ThreadPopover anchor={menuAnchor} open={menu !== null} onClose={() => setMenu(null)} label={t("code_editor.tab_menu")} width={230}>
        {menu && (
          <div role="menu">
            <ThreadMenuItem icon={<X className="h-4 w-4" />} label={t("code_editor.menu.close")} hint="Ctrl+W" onSelect={() => { setMenu(null); requestClose([menu.key]); }} />
            <ThreadMenuItem icon={<span aria-hidden />} label={t("code_editor.menu.close_others")} onSelect={() => { setMenu(null); requestClose(tabs.filter((tab) => tab.key !== menu.key).map((tab) => tab.key)); }} />
            <ThreadMenuItem icon={<span aria-hidden />} label={t("code_editor.menu.close_saved")} onSelect={() => { setMenu(null); requestClose(tabs.filter((tab) => !files[tab.fileKey]?.dirty).map((tab) => tab.key)); }} />
            <ThreadMenuItem icon={<span aria-hidden />} label={t("code_editor.menu.close_all")} onSelect={() => { setMenu(null); requestClose(tabs.map((tab) => tab.key)); }} />
            <ThreadMenuSeparator />
            <ThreadMenuItem icon={<Copy className="h-4 w-4" />} label={t("code_editor.menu.copy_path")} onSelect={() => { setMenu(null); copyText(absolute(menu.path)); }} />
            <ThreadMenuItem icon={<span aria-hidden />} label={t("code_editor.menu.copy_relative_path")} onSelect={() => { setMenu(null); copyText(menu.path); }} />
            <ThreadMenuItem icon={<FolderSearch className="h-4 w-4" />} label={t("code_editor.menu.reveal")} onSelect={() => { setMenu(null); revealInExplorer({ workspaceId, path: menu.path }); }} />
          </div>
        )}
      </ThreadPopover>

      {pending && (
        <UnsavedDialog
          count={pending.fileKeys.length}
          name={baseName(files[pending.fileKeys[0]]?.path ?? "")}
          onCancel={() => setPending(null)}
          onDiscard={() => {
            for (const key of pending.fileKeys) discardFile(key);
            useCodeEditorStore.getState().closeTabs(pending.keys);
            setPending(null);
          }}
          onSave={async () => {
            const results = await Promise.all(pending.fileKeys.map((key) => saveFile(key)));
            setPending(null);
            if (results.every(Boolean)) useCodeEditorStore.getState().closeTabs(pending.keys);
          }}
        />
      )}
      {quick}
    </>
  );
}

function ActionButton({
  onClick,
  icon,
  label,
  hint,
  iconOnly = false,
  wide = false,
}: {
  onClick: () => void;
  icon: ReactNode;
  label: string;
  hint?: string;
  iconOnly?: boolean;
  /** Keep the label visible at every window width. */
  wide?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={hint ? `${label} (${hint})` : label}
      aria-label={label}
      className="inline-flex h-6 shrink-0 items-center gap-1.5 rounded px-1.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {icon}
      {!iconOnly && <span className={wide ? "inline" : "hidden xl:inline"}>{label}</span>}
    </button>
  );
}

function Banner({ tone, text, testId, children }: { tone: "warning" | "muted"; text: string; testId: string; children?: ReactNode }) {
  return (
    <div
      role="status"
      data-testid={testId}
      className={cn(
        "flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-1.5 text-xs",
        tone === "warning" ? "border-warning/40 bg-warning/10 text-foreground" : "border-border/60 bg-secondary/60 text-muted-foreground",
      )}
    >
      <AlertTriangle className={cn("h-3.5 w-3.5 shrink-0", tone === "warning" ? "text-warning" : "text-muted-foreground")} aria-hidden />
      <span className="min-w-0 flex-1">{text}</span>
      {children}
    </div>
  );
}

function BannerButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-6 items-center rounded-md border border-border bg-background px-2 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </button>
  );
}

function StatusBar({ fileKey }: { fileKey: string }) {
  const t = useT();
  const file = useCodeEditorStore((state) => state.files[fileKey]);
  const cursor = useCodeEditorStore((state) => state.cursor);
  if (!file) return null;
  const ready = file.status === "ready" && cursor;
  return (
    <div
      data-testid="code-editor-status"
      className="flex h-6 shrink-0 items-center gap-3 border-t border-border/60 bg-sidebar px-3 text-[11px] tabular-nums text-muted-foreground"
    >
      <span className="min-w-0 flex-1 truncate">
        {file.saving ? t("code_editor.status.saving") : file.dirty ? t("code_editor.status.unsaved") : file.status === "ready" ? t("code_editor.status.saved") : ""}
      </span>
      {ready && (
        <>
          <span>
            {fill(t("code_editor.status.position"), { line: String(cursor.line), column: String(cursor.column) })}
            {cursor.selected > 0 && ` ${fill(t("code_editor.status.selected"), { count: String(cursor.selected) })}`}
          </span>
          <span>{cursor.insertSpaces ? fill(t("code_editor.status.spaces"), { size: String(cursor.tabSize) }) : fill(t("code_editor.status.tabs"), { size: String(cursor.tabSize) })}</span>
          <span>{file.encoding === "utf-8-sig" ? t("code_editor.status.utf8_bom") : "UTF-8"}</span>
          <span>{file.eol === "\r\n" ? "CRLF" : "LF"}</span>
          <span>{languageName(cursor.language)}</span>
        </>
      )}
    </div>
  );
}

function UnsavedDialog({
  count,
  name,
  onSave,
  onDiscard,
  onCancel,
}: {
  count: number;
  name: string;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}) {
  const t = useT();
  const saveButton = useRef<HTMLButtonElement>(null);
  useEffect(() => saveButton.current?.focus(), []);
  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[86] flex items-center justify-center bg-background/60 p-4"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <section
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="code-editor-unsaved-title"
        data-testid="code-editor-unsaved"
        className="w-full max-w-sm rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-float"
      >
        <h2 id="code-editor-unsaved-title" className="text-sm font-semibold text-foreground-strong">
          {count === 1 ? fill(t("code_editor.unsaved.title_one"), { file: name }) : fill(t("code_editor.unsaved.title_many"), { count: String(count) })}
        </h2>
        <p className="mt-1.5 text-xs text-muted-foreground">{t("code_editor.unsaved.body")}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="h-8 rounded-md px-3 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {t("code_editor.unsaved.cancel")}
          </button>
          <button type="button" onClick={onDiscard} className="h-8 rounded-md border border-border px-3 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {t("code_editor.unsaved.discard")}
          </button>
          <button ref={saveButton} type="button" onClick={onSave} className="h-8 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {t("code_editor.unsaved.save")}
          </button>
        </div>
      </section>
    </div>
  );
}
