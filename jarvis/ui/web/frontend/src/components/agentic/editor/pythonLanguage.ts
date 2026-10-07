import type * as Monaco from "monaco-editor/editor/editor.api";

import { useCodeEditorStore } from "@/store/codeEditor";
import { pythonIntel, type PythonCompletion, type PythonLocation, type PythonPosition } from "./editorApi";
import { fileOfUri, fileUri } from "./editorModels";

/**
 * Python completions, hovers and go-to-definition in the code editor, answered
 * by the backend's static analysis of the live buffer and the workspace
 * (jarvis/agentic_ide/python_intel.py).
 *
 * A definition in another workspace file opens that file in a tab at the
 * line, through the editor opener registered here.
 */
export function registerPythonLanguage(monaco: typeof Monaco): void {
  const kinds: Record<string, Monaco.languages.CompletionItemKind> = {
    module: monaco.languages.CompletionItemKind.Module,
    class: monaco.languages.CompletionItemKind.Class,
    function: monaco.languages.CompletionItemKind.Function,
    instance: monaco.languages.CompletionItemKind.Variable,
    statement: monaco.languages.CompletionItemKind.Variable,
    param: monaco.languages.CompletionItemKind.Variable,
    keyword: monaco.languages.CompletionItemKind.Keyword,
    property: monaco.languages.CompletionItemKind.Property,
    path: monaco.languages.CompletionItemKind.File,
  };

  const request = (model: Monaco.editor.ITextModel, position: Monaco.Position) => {
    const file = fileOfUri(model.uri);
    if (!file) return null;
    const at: PythonPosition = {
      path: file.path,
      text: model.getValue(),
      line: position.lineNumber,
      column: position.column,
    };
    return { workspaceId: file.workspaceId, at };
  };

  const signalOf = (token: Monaco.CancellationToken) => {
    const controller = new AbortController();
    token.onCancellationRequested(() => controller.abort());
    return controller.signal;
  };

  monaco.languages.registerCompletionItemProvider("python", {
    triggerCharacters: ["."],
    async provideCompletionItems(model, position, _context, token) {
      const call = request(model, position);
      if (!call) return { suggestions: [] };
      let items: PythonCompletion[] = [];
      try {
        items = await pythonIntel<PythonCompletion[]>(call.workspaceId, "complete", call.at, signalOf(token));
      } catch {
        return { suggestions: [] }; // a failed lookup just shows no suggestions
      }
      const word = model.getWordUntilPosition(position);
      const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
      return {
        suggestions: items.map((item, index) => ({
          label: item.name,
          kind: kinds[item.type] ?? monaco.languages.CompletionItemKind.Text,
          detail: item.detail,
          insertText: item.name,
          // Keep jedi's order (it ranks by relevance) under Monaco's filtering.
          sortText: String(index).padStart(5, "0"),
          range,
        })),
      };
    },
  });

  monaco.languages.registerHoverProvider("python", {
    async provideHover(model, position, token) {
      const call = request(model, position);
      if (!call || !model.getWordAtPosition(position)) return null;
      try {
        const text = await pythonIntel<string | null>(call.workspaceId, "hover", call.at, signalOf(token));
        return text ? { contents: [{ value: text }] } : null;
      } catch {
        return null;
      }
    },
  });

  monaco.languages.registerDefinitionProvider("python", {
    async provideDefinition(model, position, token) {
      const call = request(model, position);
      if (!call) return null;
      try {
        const found = await pythonIntel<PythonLocation[]>(call.workspaceId, "definition", call.at, signalOf(token));
        return found.map((location) => ({
          uri: fileUri(call.workspaceId, location.path),
          range: new monaco.Range(location.line, location.column, location.line, location.column),
        }));
      } catch {
        return null;
      }
    },
  });

  // "Go to Definition" into another file: open it as a tab at the line.
  monaco.editor.registerEditorOpener({
    openCodeEditor(_source, resource, selectionOrPosition) {
      const file = fileOfUri(resource);
      if (!file) return false;
      const start =
        selectionOrPosition && "startLineNumber" in selectionOrPosition
          ? { line: selectionOrPosition.startLineNumber, column: selectionOrPosition.startColumn }
          : selectionOrPosition
            ? { line: selectionOrPosition.lineNumber, column: selectionOrPosition.column }
            : { line: 1, column: 1 };
      useCodeEditorStore.getState().openFile(file.workspaceId, file.path, { preview: false, ...start });
      return true;
    },
  });
}
