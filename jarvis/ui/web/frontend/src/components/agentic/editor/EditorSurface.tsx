import { useEffect, useRef, type MouseEvent } from "react";
import { Loader2 } from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useCodeEditorStore, type EditorTab } from "@/store/codeEditor";
import { languageFor, loadFile, loadHead, loadHeadText, modelOf, rememberViewState, viewStateOf } from "./editorModels";
import { gutterChanges } from "./lineDiff";
import { EDITOR_OPTIONS, applyTheme, monaco } from "./monacoRuntime";
import { renderKindOf } from "./fileKinds";
import { ExtractedPreview, MediaViewer, Notice, RenderedPreview } from "./FileViewers";

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
  rendered,
  onOpenExternally,
}: {
  tab: EditorTab;
  /** Show a Markdown, HTML or SVG file rendered instead of its source. */
  rendered: boolean;
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
  const renderKind = renderKindOf(tab.path);

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

  // Gutter markers against the last commit: green added, blue modified, a red
  // notch where lines were deleted. Recomputed shortly after each edit.
  useEffect(() => {
    const editor = editorRef.current;
    const model = modelOf(tab.fileKey);
    if (tab.mode !== "edit" || !ready || !editor || !model) return;
    let alive = true;
    let base: string | null = null;
    let timer: number | undefined;
    const collection = editor.createDecorationsCollection();
    const paint = () => {
      if (!alive || base === null || editor.getModel() !== model) {
        collection.clear();
        return;
      }
      const lines = model.getLineCount();
      const changes = gutterChanges(base, model.getValue()) ?? [];
      collection.set(
        changes.map((change) => {
          const below = change.kind === "deleted" && change.start > lines;
          const start = Math.min(change.start, lines);
          return {
            range: new monaco.Range(start, 1, Math.min(change.end, lines), 1),
            options: {
              isWholeLine: true,
              linesDecorationsClassName: below ? "jarvis-gutter-deleted-below" : `jarvis-gutter-${change.kind}`,
            },
          };
        }),
      );
    };
    void loadHeadText(tab.fileKey).then((text) => {
      base = text;
      paint();
    });
    const listener = model.onDidChangeContent(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(paint, 250);
    });
    return () => {
      alive = false;
      window.clearTimeout(timer);
      listener.dispose();
      collection.clear();
    };
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
    if (reveal.length > 0) {
      editor.setSelection(new monaco.Selection(reveal.line, reveal.column, reveal.line, reveal.column + reveal.length));
    } else {
      editor.setPosition(position);
    }
    editor.revealPositionInCenter(position);
    editor.focus();
  }, [reveal, ready, tab.fileKey, tab.mode, tab.key]);

  const external = () => onOpenExternally(tab.path);
  const overlay = (() => {
    if (status === "loading") {
      return (
        <p className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("code_editor.loading")}
        </p>
      );
    }
    if (status === "ready") {
      return rendered && renderKind && tab.mode === "edit" ? (
        <RenderedPreview kind={renderKind} fileKey={tab.fileKey} workspaceId={tab.workspaceId} path={tab.path} />
      ) : null;
    }
    if (status === "image" || status === "pdf" || status === "audio" || status === "video") {
      return <MediaViewer kind={status} workspaceId={tab.workspaceId} path={tab.path} onOpenExternally={external} />;
    }
    if (status === "document" || status === "binary" || status === "too_large") {
      const note =
        status === "document" ? t("code_editor.document_note") : status === "binary" ? t("code_editor.binary") : t("code_editor.too_large");
      return <ExtractedPreview workspaceId={tab.workspaceId} path={tab.path} note={note} onOpenExternally={external} />;
    }
    return <Notice message={fill(t("code_editor.load_failed"), { error: file?.error ?? "" })} />;
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
      {overlay && <div className="absolute inset-0 bg-background">{overlay}</div>}
    </div>
  );
}
