import { useEffect, useRef, type MouseEvent } from "react";
import { ExternalLink, FileWarning, Loader2 } from "lucide-react";

import { fill, useT } from "@/i18n";
import { workspaceFileUrl } from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { useCodeEditorStore, type EditorTab } from "@/store/codeEditor";
import { languageFor, loadFile, loadHead, modelOf, rememberViewState, viewStateOf } from "./editorModels";
import { EDITOR_OPTIONS, applyTheme, monaco } from "./monacoRuntime";

type CodeEditor = monaco.editor.ICodeEditor;

/** Report the cursor, selection and indentation to the status bar. */
function reportCursor(editor: CodeEditor): void {
  const model = editor.getModel();
  const position = editor.getPosition();
  if (!model || !position) {
    useCodeEditorStore.getState().setCursor(null);
    return;
  }
  const selected = (editor.getSelections() ?? []).reduce((sum, range) => sum + model.getValueLengthInRange(range), 0);
  const options = model.getOptions();
  useCodeEditorStore.getState().setCursor({
    line: position.lineNumber,
    column: position.column,
    selected,
    language: model.getLanguageId(),
    insertSpaces: options.insertSpaces,
    tabSize: options.tabSize,
  });
}

function watchCursor(editor: CodeEditor): monaco.IDisposable[] {
  return [
    editor.onDidChangeCursorSelection(() => reportCursor(editor)),
    editor.onDidChangeModel(() => reportCursor(editor)),
    editor.onDidChangeModelOptions(() => reportCursor(editor)),
  ];
}

// Monaco draws its own context menu; the app-wide edit menu must not open on top.
const keepContextMenu = (event: MouseEvent) => event.stopPropagation();

/**
 * The text area of the code editor: one long-lived editor for every edit tab
 * (switching tabs swaps the model and restores that tab's cursor and scroll)
 * and one diff editor, created the first time a Changes entry is opened.
 */
export default function EditorSurface({
  tab,
  onOpenExternally,
}: {
  tab: EditorTab;
  onOpenExternally: (path: string) => void;
}) {
  const t = useT();
  const codeHost = useRef<HTMLDivElement>(null);
  const diffHost = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const diffRef = useRef<monaco.editor.IStandaloneDiffEditor | null>(null);
  const shown = useRef<string | null>(null);
  const handledReveal = useRef(0);
  const file = useCodeEditorStore((state) => state.files[tab.fileKey]);
  const reveal = useCodeEditorStore((state) => state.reveal);
  const status = file?.status ?? "loading";
  const ready = status === "ready";

  // Follow the app's light/dark switch. Watching the class on <html> (not a
  // prop) matters: the theme provider repaints the document in its own effect,
  // which runs after this component's, so a prop-driven re-read would still see
  // the previous theme's colours.
  useEffect(() => {
    const root = document.documentElement;
    const sync = () => applyTheme(root.classList.contains("dark"));
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class", "style"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const editor = monaco.editor.create(codeHost.current!, { ...EDITOR_OPTIONS, model: null, theme: "jarvis" });
    editorRef.current = editor;
    const watchers = watchCursor(editor);
    return () => {
      if (shown.current) rememberViewState(shown.current, editor.saveViewState());
      for (const watcher of watchers) watcher.dispose();
      editor.dispose();
      diffRef.current?.dispose();
      diffRef.current = null;
      editorRef.current = null;
      useCodeEditorStore.getState().setCursor(null);
    };
  }, []);

  useEffect(() => {
    if (status === "loading") void loadFile(tab.fileKey);
  }, [tab.fileKey, status]);

  // Edit tabs: put this tab's model into the shared editor.
  useEffect(() => {
    const editor = editorRef.current;
    const model = modelOf(tab.fileKey);
    if (tab.mode !== "edit" || !ready || !editor || !model) return;
    if (editor.getModel() !== model || shown.current !== tab.key) {
      if (shown.current && editor.getModel()) rememberViewState(shown.current, editor.saveViewState());
      editor.setModel(model);
      const state = viewStateOf(tab.key);
      if (state) editor.restoreViewState(state);
      shown.current = tab.key;
    }
    reportCursor(editor);
    editor.focus();
  }, [tab.key, tab.fileKey, tab.mode, ready]);

  // Diff tabs: the committed text on the left, the live (editable) buffer on the right.
  useEffect(() => {
    if (tab.mode !== "diff" || !ready) return;
    let alive = true;
    let watchers: monaco.IDisposable[] = [];
    void loadHead(tab.fileKey).then((original) => {
      const modified = modelOf(tab.fileKey);
      if (!alive || !original || !modified || !diffHost.current) return;
      let diff = diffRef.current;
      if (!diff) {
        diff = monaco.editor.createDiffEditor(diffHost.current, {
          ...EDITOR_OPTIONS,
          minimap: { enabled: false },
          originalEditable: false,
          renderSideBySide: true,
          useInlineViewWhenSpaceIsLimited: true,
          renderSideBySideInlineBreakpoint: 900,
          theme: "jarvis",
        });
        diffRef.current = diff;
      }
      diff.setModel({ original, modified });
      const right = diff.getModifiedEditor();
      watchers = watchCursor(right);
      reportCursor(right);
      right.focus();
    });
    return () => {
      alive = false;
      for (const watcher of watchers) watcher.dispose();
    };
  }, [tab.key, tab.fileKey, tab.mode, ready]);

  // "Open at line": a Ctrl+click on `file.py:42` in a terminal, or Quick Open's `name:42`.
  useEffect(() => {
    if (!ready || !reveal || reveal.fileKey !== tab.fileKey || reveal.nonce === handledReveal.current) return;
    const editor = tab.mode === "diff" ? diffRef.current?.getModifiedEditor() : editorRef.current;
    if (!editor || editor.getModel() !== modelOf(tab.fileKey)) return;
    handledReveal.current = reveal.nonce;
    const position = { lineNumber: reveal.line, column: reveal.column };
    editor.setPosition(position);
    editor.revealPositionInCenter(position);
    editor.focus();
  }, [reveal, ready, tab.fileKey, tab.mode, tab.key]);

  const notice = (() => {
    if (status === "loading") {
      return (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("code_editor.loading")}
        </p>
      );
    }
    if (status === "image") {
      return (
        <img
          src={workspaceFileUrl(tab.workspaceId, tab.path)}
          alt={tab.path.split("/").pop() ?? tab.path}
          className="max-h-full max-w-full rounded-md border border-border/60 object-contain"
        />
      );
    }
    if (status === "ready") return null;
    const message =
      status === "binary"
        ? t("code_editor.binary")
        : status === "too_large"
          ? t("code_editor.too_large")
          : fill(t("code_editor.load_failed"), { error: file?.error ?? "" });
    return (
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <FileWarning className="h-8 w-8 text-muted-foreground/70" aria-hidden />
        <p className="text-sm text-muted-foreground">{message}</p>
        {status !== "error" && (
          <button
            type="button"
            onClick={() => onOpenExternally(tab.path)}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-3 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            {t("code_editor.open_externally")}
          </button>
        )}
      </div>
    );
  })();

  const showCode = ready && tab.mode === "edit";
  return (
    <div className="relative h-full min-h-0 w-full" data-testid="code-editor-surface" data-language={languageFor(tab.path)}>
      <div ref={codeHost} onContextMenu={keepContextMenu} className={cn("absolute inset-0", !showCode && "invisible")} />
      <div
        ref={diffHost}
        onContextMenu={keepContextMenu}
        className={cn("absolute inset-0", tab.mode !== "diff" && "invisible", !ready && "invisible")}
      />
      {notice && <div className="absolute inset-0 flex items-center justify-center p-6">{notice}</div>}
    </div>
  );
}
