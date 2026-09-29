import { cn } from "@/lib/utils";
import type { DiffHunk } from "./explorerApi";

/**
 * A unified diff, read top to bottom: removed lines red, added lines green,
 * unchanged context in the body ink, with both line numbers in the gutter.
 */
export function DiffView({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div data-testid="explorer-diff" className="min-w-max font-mono text-[12px] leading-[1.55]">
      {hunks.map((hunk, index) => (
        <div key={`${hunk.header}-${index}`}>
          <div className="sticky top-0 z-[1] border-y border-border/60 bg-muted/80 px-3 py-1 text-[11px] text-muted-foreground backdrop-blur-sm">
            {hunk.header}
          </div>
          {hunk.lines.map((line, row) => (
            <div
              key={row}
              data-kind={line.kind}
              className={cn(
                "flex",
                line.kind === "add" && "bg-success/10",
                line.kind === "del" && "bg-destructive/10",
              )}
            >
              <span className="w-10 shrink-0 select-none pr-2 text-right tabular-nums text-muted-foreground/70">
                {line.old_no ?? ""}
              </span>
              <span className="w-10 shrink-0 select-none pr-2 text-right tabular-nums text-muted-foreground/70">
                {line.new_no ?? ""}
              </span>
              <span
                aria-hidden="true"
                className={cn(
                  "w-4 shrink-0 select-none text-center",
                  line.kind === "add" && "text-success",
                  line.kind === "del" && "text-destructive",
                )}
              >
                {line.kind === "add" ? "+" : line.kind === "del" ? "−" : ""}
              </span>
              <span
                className={cn(
                  "whitespace-pre pr-4",
                  line.kind === "add" && "text-success",
                  line.kind === "del" && "text-destructive",
                  line.kind === "ctx" && "text-foreground/80",
                )}
              >
                {line.text || " "}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
