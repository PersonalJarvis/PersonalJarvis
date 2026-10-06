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
| See what changed | The **Changes** tab, or **Show changes**: the file against the last commit, editable on the right |
| Rendered view | **Ctrl+Shift+V** for Markdown, HTML and SVG |
| Tabs | **Ctrl+W** closes, **Ctrl+Tab** switches, **Ctrl+Shift+T** reopens the last closed tab. A single click opens a preview tab (italic) that the next single click replaces; a double click or an edit keeps it |

Every file type opens:

- Text and code open in the editor (Monaco, bundled locally and loaded only
  when the first file opens).
- PDFs, images, audio and video open in built-in viewers.
- Word, Excel, PowerPoint and e-book files show the text read out of them.
- Binaries and files over 5 MB get a read-only look inside.

Non-text files also offer **Open on this computer** for editing them in their
own app.

## Working beside agents

- When an agent changes a file you have open, a clean buffer follows the disk
  by itself.
- When you have unsaved edits at that moment, a bar offers **Load their
  version** or **Keep mine**. A save never silently overwrites an agent's
  edit: every save names the version it was based on, and the server refuses
  a mismatch.
- Saves are atomic and keep the file's line endings, UTF-8 byte-order mark and
  file mode.
- Paths inside `.git` are refused, including through a symlink.

## Checklist

- [x] New File / New Folder create in the selected folder, or next to the
      selected file, or in the workspace root when nothing is selected
- [ ] Unsaved buffers survive closing or restarting the app (open tabs and
      unsaved text come back)
- [ ] Search in all files (Ctrl+Shift+F)
- [ ] Edit text files in encodings other than UTF-8 (Windows-1252, UTF-16)
- [ ] Explorer: drag to move, multi-select, copy and paste entries
- [ ] Warn before a save turns mixed line endings into one
- [ ] Verify the shortcuts on macOS (WKWebView may take Cmd+W / Cmd+Z) and
      Linux (WebKitGTK input)
- [ ] Optional: TypeScript/CSS/HTML language services, a Python language
      server, git change markers in the gutter

## Where it lives

- Backend: `jarvis/agentic_ide/file_editing.py` and the `/workspaces/{id}/`
  routes `text-file`, `text-file/version`, `head-text`, `file-list`,
  `entries`, `entries/move` and `entries/delete` in
  `jarvis/ui/web/agentic_ide_routes.py`.
- Frontend: `components/agentic/editor/`, `store/codeEditor.ts`, and the
  Folder and Changes tabs in
  `components/agentic/sidePanel/explorer/ExplorerPanel.tsx`.
