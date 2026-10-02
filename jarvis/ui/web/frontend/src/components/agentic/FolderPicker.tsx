/**
 * Which folder the agents work in.
 *
 * Seven ways to arrive at one, because people arrive differently:
 *
 * 1. **The system folder window** — Explorer on Windows, Finder on macOS, the
 *    desktop's own dialog on Linux. The one people already know. Offered only
 *    when the backend confirms this machine can actually show it (see below).
 * 2. **Recents** — the folders opened before, with their previous layout. One
 *    click and the launcher already knows how many terminals to open.
 * 3. **Search** — type a name, get matches from anywhere under home and the
 *    usual code directories. Faster than clicking down five levels. The same
 *    field reads a PATH as a place, not a name: `C:\Users\me\.claude` opens
 *    that folder on the spot instead of searching the disk for a folder called
 *    that (which found nothing, and was reported as "search is broken").
 * 4. **Browsing** — the list, projects and repositories sorted first. Hidden
 *    folders (`.claude`, `.config`) stay out of the way until asked for.
 * 5. **Typing a path** — the address bar turns into a field with completion,
 *    the way `cd` works in a terminal.
 * 6. **Drag and drop** — drop a folder (or a file inside it) anywhere on the
 *    panel. Inside the desktop shell the host reports the real path
 *    (`waitForNativeDrop`); in a browser only the NAME is known, and the
 *    backend searches for it — see `extractDropPayload`.
 * 7. **A new folder** — a project that does not exist yet has nothing to pick.
 *    "New folder" makes one inside the folder on screen, and a typed path that
 *    is not a folder yet is offered for creation rather than only refused.
 *
 * The in-page browser is not a fallback for the system window, it is the floor:
 * the system window opens on the machine the BACKEND runs on, so it is useless
 * over a network and absent on a headless server. Everything here therefore
 * works without it, and the button appears only where it can deliver.
 *
 * ## Laid out like a file window
 *
 * Every route answers the same question, so they share one shape — the one
 * people know from Explorer and Finder — instead of seven bordered regions:
 *
 * * a toolbar: search, the system window, a new folder;
 * * one panel: an address bar over the folder list. The bar shows where you
 *   are as clickable crumbs and turns into a path field when clicked, which is
 *   where typing a path lives (it used to be a second text field under the
 *   list that looked exactly like the search above it);
 * * the choice, stated once under the panel.
 *
 * **The folder on screen is the folder chosen.** Opening a folder, clicking a
 * crumb, or going up all choose what is then on screen, so "what will be used"
 * never differs from "where I am" — the old list chose a folder on click and
 * then showed its CONTENTS, which left people looking at the wrong thing and
 * guessing what the button would connect. The start view is the one exception:
 * it is a list of places, not a folder, and choosing nothing there keeps the
 * previous choice.
 *
 * Recents belong to the start view only. Pinned above every folder listing,
 * they pushed what someone was browsing half a screen down; one click on the
 * machine's name brings them back.
 *
 * The start view is labelled with the machine's own name rather than the account
 * folder: "Administrator" says nothing about which computer you are looking at,
 * "Ruben's MacBook" does.
 *
 * ## Escape
 *
 * Every host is a dialog that closes on Escape. The new-folder field and the
 * path field use Escape to back out of THEMSELVES, so they carry
 * `data-escape-local`; a host checks for it before closing (AgenticIdeView's
 * key handler, `onEscapeKeyDown` on the Radix dialogs).
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  CornerDownLeft,
  CornerLeftUp,
  Eye,
  EyeOff,
  Folder,
  FolderGit2,
  FolderOpen,
  FolderPlus,
  History,
  Loader2,
  Monitor,
  Pencil,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { waitForNativeDrop } from "@/lib/nativeDrop";
import { useDragSessionEnd } from "./dragSessionEnd";
import { Button, Field, IconButton, SectionLabel } from "./controls";
import {
  createFolder,
  fetchFolders,
  fetchNativePickerSupport,
  fetchRecents,
  forgetRecent,
  openNativePicker,
  resolveDroppedFolder,
  searchFolders,
  type FolderItem,
  type NativePickerSupport,
  type RecentWorkspace,
} from "@/lib/agenticIdeApi";

interface FolderPickerProps {
  selected: string | null;
  onSelect: (path: string) => void;
  /** A recent workspace was picked — its previous layout can be replayed. */
  onSelectRecent?: (recent: RecentWorkspace) => void;
  /**
   * Whether the picker states the chosen folder under its list. A host that
   * already reports the choice beside its own action (the launcher's step
   * list) turns it off so the folder is not named twice.
   */
  showSelection?: boolean;
  className?: string;
}

/** Below this many characters the search stays a local filter of the open list. */
const SEARCH_MIN = 2;
const SEARCH_DEBOUNCE_MS = 250;
/** More crumbs than this and the middle ones fold into "…". */
const CRUMBS_MAX = 4;

/**
 * Pull whatever identifies the dropped folder out of a DataTransfer.
 *
 * This MUST run synchronously inside the drop handler: a DataTransfer is
 * emptied as soon as the event returns, so reading it after an `await` yields
 * nothing. Three sources, in order of how much they tell us:
 *
 * 1. `text/uri-list` / `text/plain` — Explorer and Finder usually put the real
 *    path here, which resolves exactly.
 * 2. `webkitGetAsEntry()` — gives the folder's NAME (never its path; that is a
 *    deliberate browser restriction), which the backend can search for.
 * 3. `webkitRelativePath` of a dropped file — its first segment is the folder
 *    name, which covers dropping a file from inside the project.
 */
export function extractDropPayload(dt: DataTransfer | null): {
  path?: string;
  name?: string;
} {
  if (!dt) return {};
  const out: { path?: string; name?: string } = {};

  const uri = dt.getData("text/uri-list") || dt.getData("text/plain");
  if (uri) {
    const first = uri.split(/[\r\n]+/).find((line) => line.trim().length > 0);
    if (first) out.path = first.trim();
  }

  const items = dt.items ? Array.from(dt.items) : [];
  for (const item of items) {
    const getAsEntry = (
      item as DataTransferItem & {
        webkitGetAsEntry?: () => { isDirectory?: boolean; name?: string } | null;
      }
    ).webkitGetAsEntry;
    const entry =
      typeof getAsEntry === "function" ? getAsEntry.call(item) : null;
    if (entry?.isDirectory && entry.name) {
      out.name = entry.name;
      break;
    }
  }

  if (!out.name && dt.files && dt.files.length > 0) {
    const rel = (dt.files[0] as File & { webkitRelativePath?: string })
      .webkitRelativePath;
    if (rel && rel.includes("/")) out.name = rel.split("/")[0];
  }

  return out;
}

export function FolderPicker({
  selected,
  onSelect,
  onSelectRecent,
  showSelection = true,
  className,
}: FolderPickerProps) {
  const [path, setPath] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<FolderItem[]>([]);
  const [deviceName, setDeviceName] = useState<string>("");
  const [home, setHome] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<FolderItem[] | null>(null);
  const [searching, setSearching] = useState(false);

  const [recents, setRecents] = useState<RecentWorkspace[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [dropNote, setDropNote] = useState<string | null>(null);
  const [dropChoices, setDropChoices] = useState<FolderItem[]>([]);
  const dragDepth = useRef(0);

  const [nativePicker, setNativePicker] = useState<NativePickerSupport | null>(
    null,
  );
  const [nativeOpen, setNativeOpen] = useState(false);
  const [nativeNote, setNativeNote] = useState<string | null>(null);

  // Dot-folders are noise in a project list and the one thing someone opening
  // `.claude` needs — so they are a switch, off by default, and a typed name
  // that starts with a dot turns them on for that lookup by itself.
  const [showHidden, setShowHidden] = useState(false);
  const showHiddenRef = useRef(showHidden);
  showHiddenRef.current = showHidden;

  // The search field holding a path whose last segment is not a folder (yet):
  // the folder above it is on screen, filtered to names starting with this.
  const [pathLeaf, setPathLeaf] = useState<string | null>(null);
  // Where a folder that does not exist could be made, and what to call it.
  const [createOffer, setCreateOffer] = useState<{
    parent: string | null;
    name: string;
  } | null>(null);
  const [naming, setNaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createNote, setCreateNote] = useState<string | null>(null);
  // The address bar is showing the path field instead of the crumbs.
  const [editingPath, setEditingPath] = useState(false);

  // Read through a ref by the search effect: the launcher may hand in a fresh
  // callback every render, and an effect keyed on it would re-run — and
  // re-fetch — on each of its own state updates.
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  const load = useCallback(async (target: string | null, hidden?: boolean) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchFolders(target, hidden ?? showHiddenRef.current);
      setPath(res.path);
      setParent(res.parent);
      setEntries(res.entries);
      if (res.device_name) setDeviceName(res.device_name);
      // The start view names the home folder; its path is what an example
      // path is built from, so the hint shows THIS machine's spelling —
      // `C:\Users\me\…` here, `/Users/me/…` on a Mac — not a guess.
      if (target === null) {
        const homeEntry = res.entries.find((e) => e.name === "Home");
        if (homeEntry) setHome(homeEntry.path);
      }
      if (res.error) setError(res.error);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(null);
    void fetchRecents()
      .then((res) => {
        setRecents(res.recents);
        if (res.device_name) setDeviceName(res.device_name);
      })
      .catch(() => {
        /* no recents yet — the group just stays hidden */
      });
    void fetchNativePickerSupport()
      .then(setNativePicker)
      .catch(() => {
        // An older backend has no such route. Treat that as "not available"
        // rather than showing a button that would 404 on click.
        setNativePicker({ available: false });
      });
  }, [load]);

  /**
   * Go to a folder, and — because the folder on screen is the folder chosen —
   * choose it. `null` is the start view, which is a list of places rather
   * than a folder, so it leaves the choice as it was.
   */
  const navigate = (target: string | null) => {
    if (target) onSelect(target);
    setQuery("");
    setSearchHits(null);
    setPathLeaf(null);
    setCreateOffer(null);
    setEditingPath(false);
    void load(target);
  };

  /**
   * Hand over to the operating system's own folder window.
   *
   * The request stays open for as long as the window does — a person deciding
   * where their project lives is not a slow response — so the button reports
   * that a window is waiting for them. Without it a dialog that opened behind
   * the app window looks exactly like a button that did nothing.
   */
  const browseNatively = () => {
    if (nativeOpen) return;
    setNativeOpen(true);
    setNativeNote(null);
    void openNativePicker(selected ?? path)
      .then((res) => {
        if (res.path) {
          navigate(res.path);
        } else if (res.error) {
          setNativeNote(res.error);
        }
        // A cancelled dialog says nothing: the user closed it on purpose and
        // whatever was selected before is still selected.
      })
      .catch((e) => setNativeNote((e as Error).message))
      .finally(() => setNativeOpen(false));
  };

  /**
   * A path typed into the search field is a place to go, not a name to find.
   *
   * The folder is listed and becomes the selection when it exists. When it does
   * not, the folder ABOVE it is listed instead, narrowed to names starting with
   * the last segment — so a half-typed name completes on screen — and, once
   * nothing matches, creating it is offered. Nothing here is relative to the
   * folder that happened to be open: a typed path means exactly what it says.
   */
  const goToTypedPath = useCallback((typed: string) => {
    const { dir, leaf } = splitTypedPath(typed);
    const hidden = showHiddenRef.current || leaf.startsWith(".");
    return fetchFolders(typed, hidden).then(async (res) => {
      if (res.device_name) setDeviceName(res.device_name);
      if (!res.error) {
        setPath(res.path);
        setParent(res.parent);
        setEntries(res.entries);
        setPathLeaf(null);
        setCreateOffer(null);
        setError(null);
        if (res.path) onSelectRef.current(res.path);
        return;
      }
      if (!dir || !leaf) {
        setError(res.error);
        setPathLeaf(null);
        setCreateOffer(null);
        return;
      }
      const above = await fetchFolders(dir, hidden);
      if (above.error) {
        setError(above.error);
        setPathLeaf(null);
        setCreateOffer(null);
        return;
      }
      setPath(above.path);
      setParent(above.parent);
      setEntries(above.entries);
      setError(null);
      setPathLeaf(leaf);
      setCreateOffer({ parent: above.path, name: leaf });
    });
  }, []);

  // Server-side search once the query is long enough; shorter input just filters
  // the folder list already on screen, which feels instant. A path goes its own
  // way (see `goToTypedPath`) and never reaches the name search.
  useEffect(() => {
    const typed = normalizeTypedPath(query);
    if (looksLikePath(typed)) {
      setSearchHits(null);
      setSearching(true);
      const handle = window.setTimeout(() => {
        goToTypedPath(typed)
          .catch((e) => setError((e as Error).message))
          .finally(() => setSearching(false));
      }, SEARCH_DEBOUNCE_MS);
      return () => window.clearTimeout(handle);
    }
    setPathLeaf(null);
    setCreateOffer(null);
    const trimmed = query.trim();
    if (trimmed.length < SEARCH_MIN) {
      setSearchHits(null);
      setSearching(false);
      return;
    }
    setSearching(true);
    const handle = window.setTimeout(() => {
      searchFolders(trimmed)
        .then((res) => setSearchHits(res.entries))
        .catch(() => setSearchHits([]))
        .finally(() => setSearching(false));
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [query, goToTypedPath]);

  const typedPath = looksLikePath(normalizeTypedPath(query));
  // A bare word narrows what is on screen; a path is a place, not a filter.
  const needle = typedPath ? "" : query.trim().toLowerCase();

  const visible = useMemo(() => {
    if (searchHits !== null) return searchHits;
    if (pathLeaf !== null) {
      const leafNeedle = pathLeaf.toLowerCase();
      return entries.filter((e) => e.name.toLowerCase().startsWith(leafNeedle));
    }
    if (!needle) return entries;
    return entries.filter((e) => e.name.toLowerCase().includes(needle));
  }, [entries, needle, searchHits, pathLeaf]);

  const searchingMachine = searchHits !== null;
  const atStart = path === null && pathLeaf === null && !searchingMachine;
  const visibleRecents = useMemo(
    () =>
      atStart
        ? recents.filter(
            (r) =>
              !needle ||
              r.name.toLowerCase().includes(needle) ||
              r.path.toLowerCase().includes(needle),
          )
        : [],
    [atStart, recents, needle],
  );

  /** Make the folder, then treat it like any other folder that was opened. */
  const makeFolder = (parent: string | null, name: string) => {
    if (creating) return;
    setCreating(true);
    setCreateNote(null);
    createFolder({ parent, name })
      .then((res) => {
        if (res.error || !res.folder) {
          setCreateNote(res.error || "Could not create that folder.");
          return;
        }
        setNaming(false);
        setNewName("");
        setError(null);
        navigate(res.folder.path);
      })
      .catch((e) => setCreateNote((e as Error).message))
      .finally(() => setCreating(false));
  };

  const toggleHidden = () => {
    const next = !showHidden;
    setShowHidden(next);
    showHiddenRef.current = next;
    if (searchHits === null) void load(path, next);
  };

  /**
   * A typed path is checked before it is chosen. The old order — select first,
   * then list — let "cd haral\.personal-jarvis" (not a folder anywhere) reach
   * the review step as the workspace, with the error shown next to it. Now the
   * folder is listed first; only when it really is one does it become the
   * selection, and otherwise the message stays and the previous choice stands.
   *
   * Resolves to whether it worked, so the path field stays open for a fix.
   */
  const usePath = (raw: string): Promise<boolean> => {
    const value = raw.trim();
    if (!value) return Promise.resolve(false);
    setLoading(true);
    setError(null);
    const { dir, leaf } = splitTypedPath(value);
    return fetchFolders(value, showHiddenRef.current || leaf.startsWith("."))
      .then((res) => {
        if (res.device_name) setDeviceName(res.device_name);
        if (res.error) {
          setError(res.error);
          // Not a folder — but it could be one. The offer sits next to the
          // message, so a typo is corrected and a new project is created from
          // the same line.
          setCreateOffer(dir && leaf ? { parent: dir, name: leaf } : null);
          return false;
        }
        setPath(res.path);
        setParent(res.parent);
        setEntries(res.entries);
        setCreateOffer(null);
        setPathLeaf(null);
        onSelect(res.path ?? value);
        return true;
      })
      .catch((e) => {
        setError((e as Error).message);
        return false;
      })
      .finally(() => setLoading(false));
  };

  const pickRecent = (recent: RecentWorkspace) => {
    navigate(recent.path);
    onSelectRecent?.(recent);
  };

  // ------------------------------------------------------------ drag & drop
  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    setDropChoices([]);
    // Read the DataTransfer BEFORE awaiting anything (see extractDropPayload).
    const payload = extractDropPayload(event.dataTransfer);
    if (!payload.path && !payload.name) {
      setDropNote(
        "That was not a folder. Drop a folder here, or pick one from the list.",
      );
      return;
    }
    setDropNote("Finding the folder you dropped…");
    // The desktop shell knows the real path of what was dropped and says so a
    // moment after the drop; with only a name, that beats searching for it.
    // The wait itself is armed here, synchronously, so no announcement is
    // missed — outside the shell it answers null at once.
    const nativePaths = payload.path
      ? Promise.resolve(null)
      : waitForNativeDrop({ name: payload.name });
    void nativePaths
      .then((native) =>
        resolveDroppedFolder(
          native?.paths[0] ? { path: native.paths[0] } : payload,
        ),
      )
      .then((res) => {
        if (res.resolved) {
          setDropNote(res.detail || null);
          navigate(res.resolved);
        } else {
          setDropChoices(res.candidates);
          setDropNote(res.detail || "Could not find that folder.");
        }
      })
      .catch((e) => setDropNote((e as Error).message));
  };

  // The same backstop every other drop target in the app needs: a drag that
  // ends outside the window sends no `dragleave`, `drop` or `dragend`, and the
  // highlight would otherwise sit over the folder list for good (BUG-167).
  const clearDragOver = useCallback(() => {
    dragDepth.current = 0;
    setDragOver(false);
  }, []);
  useDragSessionEnd(dragOver, clearDragOver);

  // What a full path looks like on THIS machine, for the "nothing found"
  // hints. Built from the real home folder, never from a hard-coded `C:\…` —
  // the same UI is opened against Macs and Linux boxes.
  const pathExample = home ? joinPath(home, "my-project") : null;
  const machine = deviceName || "This machine";
  const crumbs = path ? foldCrumbs(pathCrumbs(path)) : [];
  const listLabel = searchingMachine
    ? null
    : pathLeaf !== null
      ? `Folders starting with “${pathLeaf}”`
      : path
        ? `Folders in ${leafName(path)}`
        : "Places";
  const hasMessages = Boolean(error || nativeNote || dropNote || createNote);

  return (
    <div
      onDragEnter={(e) => {
        e.preventDefault();
        dragDepth.current += 1;
        setDragOver(true);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragOver(false);
      }}
      onDrop={onDrop}
      data-testid="folder-drop-zone"
      className={cn("relative flex min-h-0 flex-1 flex-col p-3", className)}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-40 flex flex-col items-center justify-center gap-2 rounded-surface border-2 border-dashed border-primary/60 bg-background/95">
          <FolderOpen className="h-8 w-8 text-primary" />
          <span className="text-sm font-medium text-foreground">
            Drop to choose this folder
          </span>
        </div>
      )}

      {/* ------------------------------------------------------------ toolbar */}
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          {searching ? (
            <Loader2 className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : (
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          )}
          <Field
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name or paste a path"
            aria-label="Search folders by name, or paste a path"
            data-testid="folder-search"
            data-autofocus
            className="h-9 w-full pl-9 pr-8"
            spellCheck={false}
            autoComplete="off"
          />
          {query && (
            <IconButton
              size="sm"
              label="Clear search"
              onClick={() => setQuery("")}
              className="absolute right-1.5 top-1/2 -translate-y-1/2"
            >
              <X className="h-3.5 w-3.5" />
            </IconButton>
          )}
        </div>
        {nativePicker?.available && (
          <Button
            onClick={browseNatively}
            disabled={nativeOpen}
            data-testid="native-browse"
            title="Choose the folder in this computer's own folder window"
            className="h-9"
          >
            {nativeOpen ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <FolderOpen className="h-4 w-4" />
            )}
            {nativeOpen ? "Window open…" : "Browse…"}
          </Button>
        )}
        <Button
          onClick={() => {
            setNaming(true);
            setNewName("");
            setCreateNote(null);
          }}
          disabled={naming}
          data-testid="new-folder"
          title={`Make a new folder in ${path ?? "your home folder"}`}
          className="h-9"
        >
          <FolderPlus className="h-4 w-4" />
          New folder
        </Button>
      </div>

      {/* The window can still end up behind the app despite being opened
          topmost — a user who does not know it is there just sees a button
          that hung. Saying so costs one line and saves the confusion. */}
      {nativeOpen && (
        <p className="mt-2 text-xs text-primary" role="status">
          A folder window has opened. Choose your folder there. If you cannot
          see it, it is behind this window or in the taskbar.
        </p>
      )}

      {/* -------------------------------------------------------------- panel */}
      {/* Not overflow-hidden: the path field's suggestions hang below the
          address bar, over the list, and would be clipped by the panel. */}
      <div className="mt-3 flex min-h-0 flex-1 flex-col rounded-surface border border-border">
        {/* ------------------------------------------------------ address bar */}
        <div className="relative flex h-10 shrink-0 items-center gap-0.5 rounded-t-surface border-b border-border bg-secondary/40 px-1">
          <IconButton
            label="Go up one folder"
            onClick={() => navigate(parent)}
            disabled={loading || searchingMachine || path === null}
          >
            <CornerLeftUp className="h-4 w-4" />
          </IconButton>

          {editingPath ? (
            <PathInput
              base={path}
              initial={path ? withTrailingSeparator(path) : ""}
              showHidden={showHidden}
              onUse={(value) =>
                usePath(value).then((ok) => {
                  if (ok) setEditingPath(false);
                })
              }
              onCancel={() => setEditingPath(false)}
            />
          ) : searchingMachine ? (
            <span className="min-w-0 flex-1 truncate px-2 text-xs text-muted-foreground">
              {visible.length === 0 && !searching
                ? `Nothing on ${machine} is called “${query.trim()}”`
                : `Folders called “${query.trim()}” anywhere on ${machine}`}
            </span>
          ) : (
            <nav
              aria-label="Folder path"
              className="flex h-full min-w-0 flex-1 items-center"
            >
              <button
                type="button"
                onClick={() => navigate(null)}
                title={`Start: recent folders and places on ${machine}`}
                aria-current={path === null ? "location" : undefined}
                className={cn(
                  CRUMB,
                  "max-w-[11rem] shrink",
                  path === null && "font-medium text-foreground",
                )}
              >
                <Monitor className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{machine}</span>
              </button>
              {crumbs.map((crumb, index) => {
                const last = index === crumbs.length - 1;
                return (
                  <Fragment key={crumb.path ?? `fold-${index}`}>
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
                    {crumb.path === null ? (
                      <button
                        type="button"
                        onClick={() => setEditingPath(true)}
                        title={`${crumb.hidden}\nClick to see and edit the whole path`}
                        aria-label="Show the whole path"
                        className={cn(CRUMB, "shrink-0")}
                      >
                        …
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => navigate(crumb.path)}
                        title={crumb.path}
                        aria-current={last ? "location" : undefined}
                        className={cn(
                          CRUMB,
                          "min-w-[2.5rem] max-w-[12rem] shrink",
                          last && "font-medium text-foreground",
                        )}
                      >
                        <span className="truncate">{crumb.label}</span>
                      </button>
                    )}
                  </Fragment>
                );
              })}
              {/* The empty rest of the bar is where a path is typed, the way
                  clicking beside the crumbs in Explorer turns them into text. */}
              <button
                type="button"
                tabIndex={-1}
                aria-hidden="true"
                onClick={() => setEditingPath(true)}
                className="h-full min-w-4 flex-1 cursor-text"
              />
            </nav>
          )}

          {!editingPath && (
            <IconButton
              label="Type a path"
              title="Type or paste a path — Tab completes folder names"
              onClick={() => setEditingPath(true)}
              data-testid="edit-path"
            >
              <Pencil className="h-3.5 w-3.5" />
            </IconButton>
          )}
          <IconButton
            label={showHidden ? "Hide hidden folders" : "Show hidden folders"}
            title={
              showHidden
                ? "Hidden folders (names starting with a dot) are shown"
                : "Also list hidden folders — names starting with a dot"
            }
            aria-pressed={showHidden}
            onClick={toggleHidden}
            disabled={loading}
            data-testid="toggle-hidden"
            className={cn(showHidden && "text-primary")}
          >
            {showHidden ? (
              <Eye className="h-4 w-4" />
            ) : (
              <EyeOff className="h-4 w-4" />
            )}
          </IconButton>
          <IconButton
            label="Reload this folder"
            onClick={() => void load(path)}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
          </IconButton>
        </div>

        {/* ------------------------------------------------------- new folder */}
        {naming && (
          <form
            data-testid="new-folder-form"
            data-escape-local
            className="shrink-0 border-b border-border px-3 py-2.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) makeFolder(path, newName);
            }}
          >
            <div className="flex items-center gap-2">
              <FolderPlus className="h-4 w-4 shrink-0 text-primary" />
              <Field
                autoFocus
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setNaming(false);
                    setCreateNote(null);
                  }
                }}
                placeholder="Name of the new folder"
                aria-label="Name of the new folder"
                data-testid="new-folder-name"
                className="min-w-0 flex-1"
                spellCheck={false}
                autoComplete="off"
              />
              <Button
                type="submit"
                variant="primary"
                disabled={creating || !newName.trim()}
                data-testid="new-folder-create"
              >
                {creating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                Create
              </Button>
              <IconButton
                label="Cancel new folder"
                onClick={() => {
                  setNaming(false);
                  setCreateNote(null);
                }}
              >
                <X className="h-4 w-4" />
              </IconButton>
            </div>
            <p className="mt-1.5 truncate pl-6 text-xs text-muted-foreground">
              Created in{" "}
              <span className="font-mono">{path ?? "your home folder"}</span>
            </p>
          </form>
        )}

        {/* ------------------------------------------------------------- list */}
        <div className="min-h-[14rem] flex-1 overflow-y-auto scrollbar-jarvis p-1.5">
          {visibleRecents.length > 0 && (
            <section aria-label="Recent folders" className="pb-2">
              <GroupLabel>Recent folders</GroupLabel>
              <ul>
                {visibleRecents.map((recent) => (
                  <li key={recent.path} className="group/row relative">
                    <button
                      type="button"
                      onClick={() => pickRecent(recent)}
                      title={recent.path}
                      className={cn(
                        ROW,
                        "pr-9",
                        selected === recent.path
                          ? "bg-primary/10"
                          : "hover:bg-secondary",
                      )}
                    >
                      <History
                        data-testid="recent-folder-icon"
                        className="h-4 w-4 shrink-0 text-muted-foreground"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">
                          {recent.name}
                        </span>
                        <span className="block truncate font-mono text-micro text-muted-foreground">
                          {recent.path}
                        </span>
                      </span>
                      <span className="shrink-0 text-right text-micro text-muted-foreground">
                        {lastUsedLabel(recent.last_used)}
                        {/* The terminal count only means something where the
                            layout is replayed — the launcher. */}
                        {onSelectRecent && (
                          <span className="block tabular-nums">
                            {recent.terminals}{" "}
                            {recent.terminals === 1 ? "terminal" : "terminals"}
                          </span>
                        )}
                      </span>
                    </button>
                    <IconButton
                      size="sm"
                      label={`Forget ${recent.name}`}
                      title="Remove from this list (the folder itself stays)"
                      onClick={(e) => {
                        e.stopPropagation();
                        setRecents((prev) =>
                          prev.filter((r) => r.path !== recent.path),
                        );
                        void forgetRecent(recent.path).catch(() => {
                          /* list refreshes on next visit */
                        });
                      }}
                      className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 focus-visible:opacity-100 group-hover/row:opacity-100"
                    >
                      <X className="h-3.5 w-3.5" />
                    </IconButton>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/*
            The typed path's last segment is not a folder in the folder on
            screen, and nothing there starts with it either: the one thing left
            to do with that name is make it.
          */}
          {createOffer && pathLeaf !== null && visible.length === 0 && !searching && (
            <button
              type="button"
              data-testid="create-offer"
              onClick={() => makeFolder(createOffer.parent, createOffer.name)}
              disabled={creating}
              className={cn(ROW, "text-primary hover:bg-secondary")}
            >
              {creating ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
              ) : (
                <FolderPlus className="h-4 w-4 shrink-0" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate">
                  Create folder “{createOffer.name}”
                </span>
                <span className="block truncate font-mono text-micro text-muted-foreground">
                  in {createOffer.parent ?? "your home folder"}
                </span>
              </span>
            </button>
          )}

          {listLabel && visible.length > 0 && (
            <GroupLabel>{listLabel}</GroupLabel>
          )}

          {visible.length === 0 && !loading && !searching ? (
            <p className="px-2.5 py-6 text-center text-sm text-muted-foreground">
              {searchingMachine
                ? `No folder with that name was found.${
                    pathExample
                      ? ` To open one by where it is, type its full path — like ${pathExample}.`
                      : ""
                  }`
                : pathLeaf !== null
                  ? `No folder here starts with “${pathLeaf}”.`
                  : needle
                    ? `No folder here matches “${query.trim()}”. Keep typing to search the whole machine.`
                    : path
                      ? "This folder has no folders inside. You can choose it as it is, or make a new folder in it."
                      : "Nothing to list here — search above, drop a folder, or type its path."}
            </p>
          ) : (
            <ul>
              {visible.map((item) => {
                const isSelected = selected === item.path;
                const Icon = item.is_repo ? FolderGit2 : Folder;
                return (
                  <li key={item.path}>
                    <button
                      type="button"
                      onClick={() => navigate(item.path)}
                      title={item.path}
                      className={cn(
                        ROW,
                        "group/item",
                        isSelected ? "bg-primary/10" : "hover:bg-secondary",
                      )}
                    >
                      <Icon
                        className={cn(
                          "h-4 w-4 shrink-0",
                          isSelected ? "text-primary" : "text-muted-foreground",
                        )}
                      />
                      <span className="min-w-0 flex-1">
                        <span
                          className={cn(
                            "block truncate",
                            isSelected && "font-medium text-primary",
                          )}
                        >
                          {item.name}
                        </span>
                        {searchingMachine && (
                          <span className="block truncate font-mono text-micro text-muted-foreground">
                            {item.path}
                          </span>
                        )}
                      </span>
                      {item.is_repo && (
                        <span className="shrink-0 rounded border border-border px-1.5 font-mono text-micro text-muted-foreground">
                          git
                        </span>
                      )}
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover/item:opacity-100" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------------ choice */}
      {showSelection && <SelectedFolder selected={selected} />}

      {/* ---------------------------------------------------------- reporting */}
      {hasMessages ? (
        <div className="mt-2 space-y-1.5">
          {error && (
            <div
              className="rounded-control border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs"
              role="alert"
            >
              <p className="text-destructive">{error}</p>
              {pathExample && (
                <p className="mt-0.5 text-muted-foreground">
                  A full path on this machine looks like{" "}
                  <code className="font-mono">{pathExample}</code>.
                </p>
              )}
              {/* The path field's "no such folder", answered: make it. */}
              {createOffer && pathLeaf === null && (
                <Button
                  onClick={() => makeFolder(createOffer.parent, createOffer.name)}
                  disabled={creating}
                  data-testid="create-offer"
                  className="mt-2 h-7 text-xs"
                >
                  {creating ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <FolderPlus className="h-3.5 w-3.5" />
                  )}
                  Create folder “{createOffer.name}”
                </Button>
              )}
            </div>
          )}
          {createNote && (
            <p className="text-xs text-destructive" role="alert">
              {createNote}
            </p>
          )}
          {nativeNote && !nativeOpen && (
            <p className="text-xs text-muted-foreground" role="alert">
              {nativeNote}
            </p>
          )}
          {dropNote && (
            <>
              <p className="text-xs text-muted-foreground">{dropNote}</p>
              {dropChoices.length > 0 && (
                <ul>
                  {dropChoices.map((choice) => (
                    <li key={choice.path}>
                      <button
                        type="button"
                        onClick={() => {
                          setDropNote(null);
                          setDropChoices([]);
                          navigate(choice.path);
                        }}
                        className="flex w-full items-center gap-2 rounded-control px-1.5 py-1 text-left text-xs hover:bg-secondary"
                      >
                        <FolderOpen className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <code className="min-w-0 truncate font-mono">
                          {choice.path}
                        </code>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      ) : (
        <p className="mt-2 text-xs text-muted-foreground">
          Tip: you can also drag a folder here from your file manager.
        </p>
      )}
    </div>
  );
}

const CRUMB =
  "inline-flex h-7 min-w-0 items-center gap-1.5 rounded-control px-1.5 text-xs " +
  "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60";

const ROW =
  "flex min-h-9 w-full items-center gap-3 rounded-control px-2.5 py-1.5 text-left text-sm " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60";

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-2.5 pb-1 pt-2">
      <SectionLabel>{children}</SectionLabel>
    </div>
  );
}

/**
 * The folder that will be used, said once, right above whatever acts on it.
 *
 * The list alone never answered this: a folder that is opened is chosen AND
 * replaced on screen by its contents, so the one row that could have shown
 * the choice is gone the moment it is made.
 */
function SelectedFolder({ selected }: { selected: string | null }) {
  return (
    <div
      data-testid="folder-selection"
      role="status"
      aria-live="polite"
      className={cn(
        "mt-3 flex min-h-[3.25rem] shrink-0 items-center gap-3 rounded-surface border px-3 py-2",
        selected ? "border-primary/40 bg-primary/5" : "border-dashed border-border",
      )}
    >
      {selected ? (
        <>
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <Check className="h-3.5 w-3.5" strokeWidth={3} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">
              <span className="sr-only">Chosen folder: </span>
              {leafName(selected)}
            </span>
            <span
              className="block truncate font-mono text-micro text-muted-foreground"
              title={selected}
            >
              {selected}
            </span>
          </span>
        </>
      ) : (
        <>
          <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">
            No folder chosen yet. Open a folder in the list to choose it.
          </span>
        </>
      )}
    </div>
  );
}

/** Where the typed text stops being a folder and starts being what to match. */
export function splitTypedPath(value: string): { dir: string; leaf: string } {
  const cut = Math.max(value.lastIndexOf("/"), value.lastIndexOf("\\"));
  if (cut < 0) return { dir: "", leaf: value };
  return { dir: value.slice(0, cut + 1), leaf: value.slice(cut + 1) };
}

/**
 * The separator this machine uses, read off a real path instead of guessed.
 *
 * A web page cannot know whether the backend is Windows or not, and guessing
 * from `navigator` would describe the wrong computer entirely when the UI is
 * open on a different one. Every path the backend hands out already contains
 * the answer.
 */
export function separatorOf(fullPath: string): string {
  return fullPath.includes("\\") ? "\\" : "/";
}

export function joinPath(base: string, name: string): string {
  const sep = separatorOf(base);
  return base.endsWith(sep) ? `${base}${name}` : `${base}${sep}${name}`;
}

function withTrailingSeparator(fullPath: string): string {
  return /[\\/]$/.test(fullPath) ? fullPath : `${fullPath}${separatorOf(fullPath)}`;
}

/** The last segment of a path — what a person calls the folder. */
export function leafName(fullPath: string): string {
  const trimmed = fullPath.replace(/[\\/]+$/, "");
  return trimmed.split(/[\\/]/).pop() || fullPath;
}

/**
 * A path as the crumbs of an address bar, each one a place to go.
 *
 * The root keeps its own shape — `C:` on Windows, `/` elsewhere, the whole
 * `\\server\share` of a network path, since a server alone is not a folder
 * anyone can open.
 */
export function pathCrumbs(fullPath: string): { label: string; path: string }[] {
  const sep = separatorOf(fullPath);
  let root = "";
  let rest = fullPath;
  const unc = fullPath.match(/^([\\/]{2}[^\\/]+[\\/]+[^\\/]+)[\\/]*(.*)$/);
  const drive = fullPath.match(/^([a-zA-Z]:)[\\/]*(.*)$/);
  if (unc) {
    root = unc[1];
    rest = unc[2];
  } else if (drive) {
    root = `${drive[1]}${sep}`;
    rest = drive[2];
  } else if (/^[\\/]/.test(fullPath)) {
    root = sep;
    rest = fullPath.replace(/^[\\/]+/, "");
  }
  const crumbs: { label: string; path: string }[] = [];
  if (root) {
    crumbs.push({ label: root === sep ? sep : root.replace(/[\\/]+$/, ""), path: root });
  }
  let acc = root;
  for (const part of rest.split(/[\\/]+/).filter(Boolean)) {
    acc = acc ? joinPath(acc, part) : part;
    crumbs.push({ label: part, path: acc });
  }
  return crumbs;
}

/**
 * A long path keeps its root and the two folders nearest the end; the middle
 * folds into one "…" crumb (`path: null`) that names what it hides.
 */
function foldCrumbs(
  crumbs: { label: string; path: string }[],
): { label: string; path: string | null; hidden?: string }[] {
  if (crumbs.length <= CRUMBS_MAX) return crumbs;
  const folded = crumbs.slice(1, -2);
  return [
    crumbs[0],
    { label: "…", path: null, hidden: folded[folded.length - 1].path },
    ...crumbs.slice(-2),
  ];
}

/**
 * When a recent folder was last opened, the way people say it.
 *
 * Counted in calendar days on this clock, so something opened late last night
 * is "Yesterday" this morning, not "Today". Accepts seconds (what the backend
 * stores) or milliseconds.
 */
export function lastUsedLabel(lastUsed: number, now: number = Date.now()): string {
  if (!lastUsed || !Number.isFinite(lastUsed)) return "";
  const ms = lastUsed > 1e12 ? lastUsed : lastUsed * 1000;
  const dayStart = (t: number) => {
    const d = new Date(t);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  };
  const days = Math.round((dayStart(now) - dayStart(ms)) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  if (days < 30) {
    const weeks = Math.floor(days / 7);
    return weeks === 1 ? "Last week" : `${weeks} weeks ago`;
  }
  return new Date(ms).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: days > 300 ? "numeric" : undefined,
  });
}

/**
 * What was typed, read the way a shell would read it.
 *
 * The field used to say "like cd", and people typed `cd projects` — the command
 * word is not part of the path and is dropped (with cmd's `/d`), and so are
 * quotes around a path pasted from a terminal.
 */
export function normalizeTypedPath(raw: string): string {
  let value = raw.trim();
  value = value.replace(/^cd(?:\s+\/d)?(?:\s+|$)/i, "").trim();
  // A prompt copied whole — `PS C:\Users\me>` — is the path with a shell's
  // decoration on both ends; the decoration is not part of the folder.
  value = value.replace(/^PS\s+/, "").replace(/\s*>$/, "").trim();
  const quoted = value.match(/^(["'])(.*)\1$/);
  if (quoted) value = quoted[2].trim();
  return value;
}

/** A path that names its own root: `/…`, `~…`, `C:\…`, or a UNC `\\server`. */
export function isAbsolutePath(value: string): boolean {
  return /^(?:[a-zA-Z]:[\\/]|[\\/]|~)/.test(value);
}

/**
 * Text that names a PLACE rather than a folder to look for.
 *
 * A root or a separator anywhere is the tell: nobody searches for a folder
 * called `Desktop\shop`, they mean the one at that path. A bare name stays a
 * search, which is what a search field is for.
 */
export function looksLikePath(value: string): boolean {
  return isAbsolutePath(value) || /[\\/]/.test(value);
}

/**
 * The folder a typed path means, given the folder on screen.
 *
 * Anything that does not name its own root is relative to what the list shows
 * — `haral\.personal-jarvis` typed while looking at `C:\Users` is
 * `C:\Users\haral\.personal-jarvis`, exactly as `cd` would read it.
 * Without a folder on screen (the start view) the text is passed on as typed.
 */
export function resolveTypedPath(raw: string, base: string | null): string {
  const typed = normalizeTypedPath(raw);
  if (!typed) return "";
  if (isAbsolutePath(typed) || !base) return typed;
  return joinPath(base, typed);
}

/**
 * The address bar as a path field that completes as you type, the way `cd`
 * does in a terminal.
 *
 * It opens holding the folder on screen plus a separator, so the next segment
 * can be typed straight away; a bare name still completes against the folder
 * on screen, so `cd`-style navigation works without an absolute path. Tab
 * takes the highlighted suggestion and appends a separator. Arrow keys move
 * through the list, Enter opens what is highlighted — or, with nothing
 * highlighted, whatever was typed. Escape and leaving the field put the crumbs
 * back.
 *
 * Completion is deliberately a plain folder listing rather than the name search
 * in the toolbar: `cd` shows what is in THIS folder, and a field that answered
 * with matches from across the disk would be a different tool wearing the same
 * shape.
 */
function PathInput({
  base,
  initial,
  showHidden,
  onUse,
  onCancel,
}: {
  base: string | null;
  initial: string;
  showHidden: boolean;
  onUse: (value: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [options, setOptions] = useState<FolderItem[]>([]);
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);

  const { dir, leaf } = splitTypedPath(normalizeTypedPath(value));
  // An empty `dir` means a bare name was typed: complete inside the folder the
  // list is showing, which is what makes `cd projects` work; a relative folder
  // part completes inside that folder below the one on screen.
  const lookupDir = dir
    ? isAbsolutePath(dir) || !base
      ? dir
      : joinPath(base, dir)
    : base || "";

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    // The caret at the end, ready for the next segment.
    input.setSelectionRange(input.value.length, input.value.length);
  }, []);

  useEffect(() => {
    if (!value.trim() && !base) {
      setOptions([]);
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(() => {
      // A dot-name completes against hidden folders whether or not they are
      // switched on: typing `.cl` is the request to see `.claude`.
      fetchFolders(lookupDir || null, showHidden || leaf.startsWith("."))
        .then((res) => {
          if (cancelled) return;
          const needle = leaf.toLowerCase();
          setOptions(
            res.entries.filter(
              (e) => !needle || e.name.toLowerCase().startsWith(needle),
            ),
          );
          setActive(-1);
        })
        .catch(() => {
          if (!cancelled) setOptions([]);
        });
    }, 160);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [lookupDir, leaf, value, base, showHidden]);

  /** Take a suggestion and leave the cursor ready for the next segment. */
  const complete = (item: FolderItem) => {
    setValue(`${item.path}${separatorOf(item.path)}`);
    setActive(-1);
    setOpen(true);
  };

  const submit = () => {
    if (active >= 0 && options[active]) {
      void onUse(options[active].path);
    } else {
      // A relative path is relative to what is on screen, the way cd reads it.
      const absolute = resolveTypedPath(value, base);
      if (absolute) void onUse(absolute);
    }
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Tab" && options.length > 0) {
      e.preventDefault();
      complete(options[active >= 0 ? active : 0]);
      return;
    }
    if (e.key === "ArrowDown" && options.length > 0) {
      e.preventDefault();
      setOpen(true);
      setActive((prev) => (prev + 1) % options.length);
      return;
    }
    if (e.key === "ArrowUp" && options.length > 0) {
      e.preventDefault();
      setOpen(true);
      setActive((prev) => (prev <= 0 ? options.length - 1 : prev - 1));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      // First Escape closes the suggestions, the next one the field.
      if (open && options.length > 0) setOpen(false);
      else onCancel();
    }
  };

  return (
    <div className="relative min-w-0 flex-1" data-escape-local>
      <Field
        ref={inputRef}
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        // Leaving is delayed: a click on a suggestion blurs the input first,
        // and an immediate close would remove the element under the pointer
        // before the click lands on it.
        onBlur={() =>
          window.setTimeout(() => {
            if (document.activeElement !== inputRef.current) onCancel();
          }, 150)
        }
        placeholder="Type a path — Tab completes folder names"
        aria-label="Folder path"
        data-testid="folder-path-input"
        className="h-8 w-full pr-8 font-mono text-xs"
        spellCheck={false}
        autoComplete="off"
      />
      {/*
        The field's own confirm. Enter already does this, but only for someone
        who guesses that it will — and a control that can only be reached by
        guessing is not a route, so the affordance stays.
      */}
      {value.trim() && (
        <IconButton
          size="sm"
          label="Go to this path"
          onMouseDown={(e) => e.preventDefault()}
          onClick={submit}
          className="absolute right-1 top-1/2 -translate-y-1/2 text-primary"
        >
          <CornerDownLeft className="h-3.5 w-3.5" />
        </IconButton>
      )}
      {open && options.length > 0 && (
        <ul
          data-testid="path-suggestions"
          className="absolute left-0 right-0 top-full z-30 mt-1.5 max-h-56 overflow-y-auto scrollbar-jarvis rounded-control border border-border bg-popover p-1 shadow-lg"
        >
          {options.slice(0, 40).map((item, index) => (
            <li key={item.path}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => complete(item)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-control px-2 py-1.5 text-left text-xs",
                  index === active ? "bg-primary/15" : "hover:bg-secondary",
                )}
              >
                {item.is_repo ? (
                  <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate font-mono">{item.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
