/**
 * Folder picker for step 1 of the Agentic-IDE wizard.
 *
 * A native folder dialog was not an option: the desktop app is a web UI, so a
 * native picker would exist on one OS and be missing on the others — and on a
 * headless server there is no dialog at all. This browses over REST, which
 * behaves identically on Windows, macOS, Linux and a remote box.
 *
 * Four ways to arrive at a folder, because people arrive differently:
 *
 * 1. **Recents** — the workspaces opened before, with their previous layout.
 *    One click and the wizard already knows how many terminals to open.
 * 2. **Search** — type a name, get matches from anywhere under home and the
 *    usual code directories. Faster than clicking down five levels.
 * 3. **Browsing** — the list, projects and repositories sorted first.
 * 4. **Drag and drop** — drop a folder (or a file inside it) anywhere on the
 *    panel. See `extractDropPayload` for why this needs a backend round-trip.
 *
 * The start view is labelled with the machine's own name rather than the account
 * folder: "Administrator" says nothing about which computer you are looking at,
 * "Ruben's MacBook" does.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Clock,
  CornerLeftUp,
  Folder,
  FolderGit2,
  FolderOpen,
  Laptop,
  Loader2,
  RefreshCw,
  Search,
  Terminal,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  fetchFolders,
  fetchRecents,
  forgetRecent,
  resolveDroppedFolder,
  searchFolders,
  type FolderItem,
  type RecentWorkspace,
} from "@/lib/agenticIdeApi";

interface FolderPickerProps {
  selected: string | null;
  onSelect: (path: string) => void;
  /** A recent workspace was picked — its previous layout can be replayed. */
  onSelectRecent?: (recent: RecentWorkspace) => void;
}

/** Below this many characters the search stays a local filter of the open list. */
const SEARCH_MIN = 2;
const SEARCH_DEBOUNCE_MS = 250;

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
      item as DataTransferItem & { webkitGetAsEntry?: () => { isDirectory?: boolean; name?: string } | null }
    ).webkitGetAsEntry;
    const entry = typeof getAsEntry === "function" ? getAsEntry.call(item) : null;
    if (entry?.isDirectory && entry.name) {
      out.name = entry.name;
      break;
    }
  }

  if (!out.name && dt.files && dt.files.length > 0) {
    const rel = (dt.files[0] as File & { webkitRelativePath?: string }).webkitRelativePath;
    if (rel && rel.includes("/")) out.name = rel.split("/")[0];
  }

  return out;
}

export function FolderPicker({ selected, onSelect, onSelectRecent }: FolderPickerProps) {
  const [path, setPath] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<FolderItem[]>([]);
  const [deviceName, setDeviceName] = useState<string>("");
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

  const load = useCallback(async (target: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchFolders(target);
      setPath(res.path);
      setParent(res.parent);
      setEntries(res.entries);
      if (res.device_name) setDeviceName(res.device_name);
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
        /* no recents yet — the section just stays hidden */
      });
  }, [load]);

  // Server-side search once the query is long enough; shorter input just filters
  // the folder list already on screen, which feels instant.
  useEffect(() => {
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
  }, [query]);

  const visible = useMemo(() => {
    if (searchHits !== null) return searchHits;
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((e) => e.name.toLowerCase().includes(needle));
  }, [entries, query, searchHits]);

  const open = (item: FolderItem) => {
    onSelect(item.path);
    setQuery("");
    setSearchHits(null);
    void load(item.path);
  };

  const usePath = (raw: string) => {
    const value = raw.trim();
    if (!value) return;
    onSelect(value);
    void load(value);
  };

  const pickRecent = (recent: RecentWorkspace) => {
    onSelect(recent.path);
    onSelectRecent?.(recent);
    void load(recent.path);
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
      setDropNote("That drop carried no folder — browse to it or paste its path.");
      return;
    }
    setDropNote("Resolving the dropped folder…");
    void resolveDroppedFolder(payload)
      .then((res) => {
        if (res.resolved) {
          setDropNote(res.detail || null);
          onSelect(res.resolved);
          void load(res.resolved);
        } else {
          setDropChoices(res.candidates);
          setDropNote(res.detail || "Could not resolve that folder.");
        }
      })
      .catch((e) => setDropNote((e as Error).message));
  };

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
      className={cn(
        "relative space-y-4 rounded-xl transition-colors",
        dragOver && "ring-2 ring-primary/60 ring-offset-2 ring-offset-background",
      )}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-background/80">
          <div className="flex items-center gap-2 rounded-lg border border-primary/50 bg-card px-4 py-3 text-sm font-medium text-primary">
            <FolderOpen className="h-4 w-4" />
            Drop a folder to open it here
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------ recents */}
      {recents.length > 0 && searchHits === null && (
        <section className="space-y-2">
          <h4 className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-muted-foreground">
            <Clock className="h-3.5 w-3.5" />
            Recent workspaces
          </h4>
          <ul className="grid gap-2 sm:grid-cols-2">
            {recents.map((recent) => (
              <li key={recent.path} className="group relative">
                <button
                  type="button"
                  onClick={() => pickRecent(recent)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors",
                    selected === recent.path
                      ? "border-primary/50 bg-primary/5"
                      : "border-border bg-card/60 hover:border-primary/40",
                  )}
                >
                  <FolderOpen className="h-4 w-4 shrink-0 text-primary/80" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{recent.name}</span>
                    <span className="block truncate font-mono text-[10px] text-muted-foreground">
                      {recent.path}
                    </span>
                  </span>
                  <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
                    <Terminal className="h-3 w-3" />
                    {recent.terminals}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`Forget ${recent.name}`}
                  title="Remove from this list (the folder itself stays)"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRecents((prev) => prev.filter((r) => r.path !== recent.path));
                    void forgetRecent(recent.path).catch(() => {
                      /* list refreshes on next visit */
                    });
                  }}
                  className="absolute right-1 top-1 hidden rounded p-1 text-muted-foreground hover:text-foreground group-hover:block"
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ------------------------------------------------------------- search */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1">
          {searching ? (
            <Loader2 className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : (
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          )}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search folders by name…"
            aria-label="Search folders by name"
            data-testid="folder-search"
            className="w-full rounded-lg border border-border bg-background/60 py-2 pl-9 pr-8 text-sm outline-none focus:border-primary/50"
            spellCheck={false}
          />
          {query && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => void load(parent)}
          disabled={loading || searchHits !== null || (!parent && path === null)}
          title="Go up one folder"
        >
          <CornerLeftUp className="h-4 w-4" />
          Up
        </button>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => void load(path)}
          disabled={loading}
          title="Reload this folder"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
        </button>
      </div>

      {/* ---------------------------------------------------------- breadcrumb */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        {searchHits !== null ? (
          <span>
            {visible.length === 0 && !searching
              ? `Nothing matches “${query.trim()}”`
              : `Matches for “${query.trim()}”`}
          </span>
        ) : (
          <>
            <Laptop className="h-3.5 w-3.5 shrink-0 text-primary/70" />
            <span className="shrink-0 font-medium text-foreground">
              {deviceName || "This machine"}
            </span>
            {path && (
              <code className="min-w-0 truncate rounded bg-background/60 px-2 py-0.5 font-mono">
                {path}
              </code>
            )}
          </>
        )}
      </div>

      {/* --------------------------------------------------------------- list */}
      <div className="max-h-[300px] overflow-y-auto scrollbar-jarvis rounded-xl border border-border bg-card/40">
        {visible.length === 0 && !loading && !searching ? (
          <p className="p-4 text-sm text-muted-foreground">
            {searchHits !== null
              ? "No folder with that name was found."
              : "Nothing to list here — search above, drop a folder, or paste a path below."}
          </p>
        ) : (
          <ul className="divide-y divide-border/50">
            {visible.map((item) => {
              const isSelected = selected === item.path;
              return (
                <li key={item.path}>
                  <button
                    type="button"
                    onClick={() => open(item)}
                    className={cn(
                      "flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors",
                      isSelected ? "bg-primary/10 text-foreground" : "hover:bg-background/60",
                    )}
                  >
                    {item.is_repo ? (
                      <FolderGit2 className="h-4 w-4 shrink-0 text-primary" />
                    ) : item.is_project ? (
                      <FolderOpen className="h-4 w-4 shrink-0 text-primary/70" />
                    ) : (
                      <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{item.name}</span>
                      {searchHits !== null && (
                        <span className="block truncate font-mono text-[10px] text-muted-foreground">
                          {item.path}
                        </span>
                      )}
                    </span>
                    {item.is_repo && (
                      <span className="chip shrink-0 text-[10px] uppercase tracking-wide">git</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {/* --------------------------------------------------------- drop result */}
      {dropNote && (
        <div className="space-y-2 rounded-lg border border-border bg-card/60 px-3 py-2">
          <p className="text-xs text-muted-foreground">{dropNote}</p>
          {dropChoices.length > 0 && (
            <ul className="space-y-1">
              {dropChoices.map((choice) => (
                <li key={choice.path}>
                  <button
                    type="button"
                    onClick={() => {
                      setDropNote(null);
                      setDropChoices([]);
                      open(choice);
                    }}
                    className="flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-background/60"
                  >
                    <FolderOpen className="h-3.5 w-3.5 shrink-0 text-primary/70" />
                    <code className="min-w-0 truncate font-mono">{choice.path}</code>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ----------------------------------------------------------- raw path */}
      <PathInput onUse={usePath} />

      {selected && (
        <div className="flex items-center gap-2 rounded-lg border border-primary/40 bg-primary/5 px-3 py-2 text-sm">
          <FolderOpen className="h-4 w-4 shrink-0 text-primary" />
          <span className="text-muted-foreground">Selected:</span>
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
            {selected}
          </code>
        </div>
      )}
    </div>
  );
}

function PathInput({ onUse }: { onUse: (value: string) => void }) {
  const [value, setValue] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && value.trim()) onUse(value);
        }}
        placeholder="…or paste a full folder path"
        className="min-w-0 flex-1 rounded-lg border border-border bg-background/60 px-3 py-2 font-mono text-xs outline-none focus:border-primary/50"
        spellCheck={false}
      />
      <button
        type="button"
        className="btn-ghost"
        disabled={!value.trim()}
        onClick={() => onUse(value)}
      >
        Use this path
      </button>
    </div>
  );
}
