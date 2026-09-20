import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, Loader2, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { memoryDiff } from "@/components/agentchat/toolDiff";

interface MemoryFileResponse {
  path: string;
  agent_id: string;
  content: string;
  updated_ms: number;
}

async function fetchMemoryFile(path: string): Promise<MemoryFileResponse> {
  const res = await fetch(`/api/society/memory/file?path=${encodeURIComponent(path)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as MemoryFileResponse;
}

/**
 * The editor the Updating Memory row opens: the agent's memory file with
 * the change painted red/green, scrolled to the first changed line.
 *
 * `before`/`after` come from the tool result so the red/green matches the
 * chat row exactly; the fetched file proves the change landed on disk.
 */
export function MemoryFileViewer({
  path,
  before,
  after,
  onClose,
}: {
  path: string;
  before?: string;
  after?: string;
  onClose: () => void;
}) {
  const t = useT();
  const [file, setFile] = useState<MemoryFileResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const firstChangeRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let alive = true;
    setFile(null);
    setError(null);
    closeRef.current?.focus();
    void fetchMemoryFile(path)
      .then((data) => {
        if (alive) setFile(data);
      })
      .catch((err) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      alive = false;
    };
  }, [path]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const diff = useMemo(() => {
    const b = before ?? "";
    const a = after ?? file?.content ?? "";
    if (!b && !a) return null;
    return memoryDiff("society_wiki_note", { text: "" }, JSON.stringify({ path, before: b, after: a }));
  }, [before, after, file?.content, path]);

  useEffect(() => {
    if (diff && firstChangeRef.current) {
      firstChangeRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [diff, file]);

  const name = path.split("/").at(-1) ?? path;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${t("society.chat.memory_file_title")}: ${path}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <section
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-border bg-popover text-foreground shadow-float"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex h-12 shrink-0 items-center gap-3 border-b border-border bg-card px-4">
          <FileText className="h-4 w-4 shrink-0 text-primary" aria-hidden />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{name}</div>
            <div className="truncate font-mono text-[11px] text-muted-foreground">{path}</div>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label={t("society.world.drawer_close")}
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-auto p-4">
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {t("society.chat.memory_file_failed")}: {error}
            </p>
          ) : !file && !diff ? (
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              {t("society.chat.memory_file_loading")}
            </p>
          ) : diff && diff.length > 0 ? (
            <div aria-label={t("work_trace.diff")} className="font-mono text-xs leading-6">
              {diff.map((f, i) => (
                <div key={i} className="mb-2">
                  <p className="mb-2 font-sans text-xs text-muted-foreground">{f.path || path}</p>
                  {f.lines.map((line, n) => {
                    const isFirstChange =
                      (line.kind === "add" || line.kind === "del") &&
                      !diff
                        .slice(0, i)
                        .some((prev) => prev.lines.some((l) => l.kind === "add" || l.kind === "del")) &&
                      f.lines.slice(0, n).every((l) => l.kind !== "add" && l.kind !== "del");
                    return (
                      <div
                        key={n}
                        ref={isFirstChange ? firstChangeRef : undefined}
                        className={cn(
                          "whitespace-pre-wrap rounded-sm px-2 py-0.5 [overflow-wrap:anywhere]",
                          line.kind === "add" && "diff-line-add",
                          line.kind === "del" && "diff-line-del",
                          line.kind === "ctx" && "text-muted-foreground",
                        )}
                      >
                        {line.kind === "add" ? "+ " : line.kind === "del" ? "− " : "  "}
                        {line.text}
                      </div>
                    );
                  })}
                </div>
              ))}
              {file ? (
                <p className="mt-4 font-sans text-[11px] text-muted-foreground">
                  {t("society.chat.memory_file_on_disk")}
                </p>
              ) : null}
            </div>
          ) : file ? (
            <pre className="whitespace-pre-wrap font-mono text-xs leading-6 text-foreground [overflow-wrap:anywhere]">
              {file.content}
            </pre>
          ) : null}
        </div>
      </section>
    </div>
  );
}
