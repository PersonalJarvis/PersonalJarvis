# Code editor (Agentic IDE)

The Agentic IDE has a code editor for working by hand beside the coding
agents. It lies over the terminal grid; **Terminals** in its tab bar brings the
terminals back without closing a file.

## Using it

| Action | How |
| --- | --- |
| Open a file | Click it in the side panel's **Folder** tab, or **Ctrl+P** and type part of its name (`name:42` jumps to line 42) |
| Edit and save | Type, then **Ctrl+S** (**Ctrl+Shift+S** saves every open file). A dot on the tab means unsaved changes |
| New file / new folder | The buttons in the Folder tab's header, or right-click. The entry goes into the selected folder, next to the selected file, or into the workspace root when nothing is selected. A name with slashes (`src/new.py`) creates the folders on the way |
| Rename | **F2** or right-click |
| Delete | **Del** or right-click; the entry goes to the system trash |
| Select several | **Ctrl/Cmd+click** adds a row, **Shift+click** selects a range; delete, cut, copy and drag act on the whole selection |
| Move, copy | Drag rows onto a folder (or the empty space for the root), or **Ctrl+X / Ctrl+C** then **Ctrl+V** on the target. Pasting into the same folder makes a "copy" |
| Search in all files | **Ctrl+Shift+F** opens the **Search** tab: match case, whole word, regular expressions, include/exclude globs, and **Replace all** (a second click confirms; files with unsaved changes are left out) |
| See what changed | Coloured marks in the gutter (added, modified, deleted lines against the last commit), the **Changes** tab, or **Show changes**: the file against the last commit, editable on the right |
| Rendered view | **Ctrl+Shift+V** for Markdown, HTML and SVG |
| Encoding, line endings | Click them in the status bar: reopen or save with another encoding, switch between LF and CRLF |
| Tabs | **Ctrl+W** closes, **Ctrl+Tab** switches, **Ctrl+Shift+T** reopens the last closed tab. A single click opens a preview tab (italic) that the next single click replaces; a double click or an edit keeps it |

Every file type opens:

- Text and code open in the editor (Monaco, bundled locally and loaded only
  when the first file opens), with completions, hovers and syntax errors for
  JSON, CSS/SCSS/Less, HTML and TypeScript/JavaScript, and completions,
  hovers and go-to-definition for Python (the workspace is the import root,
  so its own packages resolve). **Ctrl+Space** asks for suggestions.
- Text in other encodings opens too: UTF-16 with a byte-order mark, and
  legacy code pages (Windows-1252, Cyrillic, Greek, …) detected and saved
  back in the same encoding.
- PDFs, images, audio and video open in built-in viewers.
- Word, Excel, PowerPoint and e-book files show the text read out of them.
- Binaries and files over 5 MB get a read-only look inside.

Non-text files also offer **Open on this computer** for editing them in their
own app.

## Nothing typed is lost

- Open tabs and unsaved text survive closing or restarting the app. Unsaved
  text is backed up a moment after typing pauses (per user, per IDE instance,
  outside the workspace) and laid back over its file on the next start.
- If the file changed on disk meanwhile, the restored buffer shows the
  conflict bar instead of overwriting the newer file.
- An unsaved buffer also holds off the automatic reload after a new frontend
  build.

## Working beside agents

- When an agent changes a file you have open, a clean buffer follows the disk
  by itself.
- When you have unsaved edits at that moment, a bar offers **Load their
  version** or **Keep mine**. A save never silently overwrites an agent's
  edit: every save names the version it was based on, and the server refuses
  a mismatch.
- Saves are atomic and keep the file's line endings, encoding, byte-order
  mark and file mode. A file that mixes line endings shows a note first,
  because a save writes one ending throughout.
- Paths inside `.git` are refused, including through a symlink.

## Platforms

- Windows: verified in the desktop app's WebView2 build.
- macOS: pywebview's WKWebView host takes some Command shortcuts before the
  page. `jarvis/ui/macos_editor_keys.py` routes **Cmd+W** to the page so it
  closes an editor tab instead of the window; **Cmd+Z** reaches the editor
  through the native undo. Not yet checked on a Mac.
- Linux: pywebview's GTK and Qt hosts do not intercept keys, so the shortcuts
  reach the page directly. Not yet checked on a Linux desktop.

## Checklist

- [x] New File / New Folder create in the selected folder, or next to the
      selected file, or in the workspace root when nothing is selected
- [x] Unsaved buffers survive closing or restarting the app (open tabs and
      unsaved text come back)
- [x] Search in all files (Ctrl+Shift+F), with replace
- [x] Edit text files in encodings other than UTF-8 (Windows-1252, UTF-16, …)
- [x] Explorer: drag to move, multi-select, copy and paste entries
- [x] Warn before a save turns mixed line endings into one
- [x] Shortcuts on macOS: Cmd+W routed to the page (code-level; awaiting a
      check on a real Mac) — Linux needs no routing
- [x] TypeScript/CSS/HTML language services and git change markers in the
      gutter
- [x] Python completions, hovers and go-to-definition (F12 / Ctrl+click,
      also into other workspace files), from jedi's static analysis

## Where it lives

- Backend: `jarvis/agentic_ide/file_editing.py` (read, save, create, copy,
  move, delete, encodings), `file_search.py` (search and replace),
  `editor_backups.py` (open tabs and unsaved text), `python_intel.py`
  (Python completions, hovers, definitions), and the
  `/workspaces/{id}/` routes `text-file`, `text-file/version`, `head-text`,
  `file-list`, `search`, `search/replace`, `python/{action}`,
  `editor-state`, `entries`,
  `entries/copy`, `entries/move` and `entries/delete` in
  `jarvis/ui/web/agentic_ide_routes.py`.
- Frontend: `components/agentic/editor/`, `store/codeEditor.ts`, the Folder
  and Changes tabs in `components/agentic/sidePanel/explorer/ExplorerPanel.tsx`
  and the Search tab in `components/agentic/sidePanel/search/`.
