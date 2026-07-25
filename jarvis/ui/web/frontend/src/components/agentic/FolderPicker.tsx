/**
 * Folder picker for step 1 of the Agentic-IDE wizard.
 *
 * A native folder dialog was not an option: the desktop app is a web UI, so a
 * native picker would exist on one OS and be missing on the others — and on a
 * headless server there is no dialog at all. This browses over REST instead,
 * which behaves identically on Windows, macOS, Linux and a remote box, and it
 * keeps a plain path field for the case where the user already knows where they
 * are going (or is working on a machine with no start points worth listing).
 */
import { useCallback, useEffect, useState } from "react";
import {
  ChevronRight,
  CornerLeftUp,
  Folder,
  FolderGit2,
  FolderOpen,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { fetchFolders, type FolderItem } from "@/lib/agenticIdeApi";

interface FolderPickerProps {
  selected: string | null;
  onSelect: (path: string) => void;
}

export function FolderPicker({ selected, onSelect }: FolderPickerProps) {
  const [path, setPath] = useState<string | null>(null);
  const [parent, setParent] = useState<string | null>(null);
  const [entries, setEntries] = useState<FolderItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manual, setManual] = useState("");

  const load = useCallback(async (target: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchFolders(target);
      setPath(res.path);
      setParent(res.parent);
      setEntries(res.entries);
      if (res.error) setError(res.error);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(null);
  }, [load]);

  const open = (item: FolderItem) => {
    onSelect(item.path);
    void load(item.path);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-ghost"
          onClick={() => void load(parent)}
          disabled={loading || (!parent && path === null)}
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
        <code className="min-w-0 flex-1 truncate rounded-md bg-background/60 px-3 py-1.5 font-mono text-xs text-muted-foreground">
          {path ?? "This machine"}
        </code>
      </div>

      <div className="max-h-[320px] overflow-y-auto scrollbar-jarvis rounded-xl border border-border bg-card/40">
        {entries.length === 0 && !loading ? (
          <p className="p-4 text-sm text-muted-foreground">
            Nothing to list here — type a path below instead.
          </p>
        ) : (
          <ul className="divide-y divide-border/50">
            {entries.map((item) => {
              const isSelected = selected === item.path;
              return (
                <li key={item.path}>
                  <button
                    type="button"
                    onClick={() => open(item)}
                    className={cn(
                      "flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors",
                      isSelected
                        ? "bg-primary/10 text-foreground"
                        : "hover:bg-background/60",
                    )}
                  >
                    {item.is_repo ? (
                      <FolderGit2 className="h-4 w-4 shrink-0 text-primary" />
                    ) : item.is_project ? (
                      <FolderOpen className="h-4 w-4 shrink-0 text-primary/70" />
                    ) : (
                      <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{item.name}</span>
                    {item.is_repo && (
                      <span className="chip shrink-0 text-[10px] uppercase tracking-wide">
                        git
                      </span>
                    )}
                    <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground/60" />
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

      <div className="flex flex-wrap items-center gap-2">
        <input
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && manual.trim()) {
              onSelect(manual.trim());
              void load(manual.trim());
            }
          }}
          placeholder="…or paste a full folder path"
          className="min-w-0 flex-1 rounded-lg border border-border bg-background/60 px-3 py-2 font-mono text-xs outline-none focus:border-primary/50"
          spellCheck={false}
        />
        <button
          type="button"
          className="btn-ghost"
          disabled={!manual.trim()}
          onClick={() => {
            onSelect(manual.trim());
            void load(manual.trim());
          }}
        >
          Use this path
        </button>
      </div>

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
