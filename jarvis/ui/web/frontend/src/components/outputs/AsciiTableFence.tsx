/**
 * An ASCII grid table drawn as a real table, with the original art one click
 * away.
 *
 * A worker writing plain text builds a table out of `+---+` and `|` — the
 * shape a terminal understands. In a document that art is not a table: the
 * columns only line up while the pane is wider than the widest row, nothing
 * can be scanned by eye, and a single long cell pushes the whole grid apart.
 * Here the parsed rows become a `<table>` the reader can scroll, and the
 * Rendered / Source switch — the same control a drawn `html`/`svg` fence
 * carries — keeps the original text available and copyable.
 *
 * All colour comes from theme tokens, so the table reads in both palettes.
 */
import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";
import { robustCopy } from "@/lib/clipboard";
import { useT } from "@/i18n";
import { CodeBlock } from "@/components/docs/CodeBlock";
import type { AsciiGrid } from "@/lib/asciiTable";

const CELL = "px-3 py-1.5 align-top text-foreground/90";

export function AsciiTableFence({ grid, code }: { grid: AsciiGrid; code: string }) {
  const t = useT();
  const [mode, setMode] = useState<"rendered" | "source">("rendered");
  const [copied, setCopied] = useState(false);

  const copy = () => {
    void robustCopy(code).then((ok) => {
      if (!ok) return;
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  const segment = (value: "rendered" | "source", label: string) => (
    <button
      type="button"
      aria-pressed={mode === value}
      onClick={() => setMode(value)}
      className={cn(
        "rounded-sm px-2 py-0.5 text-[10px] font-medium transition-colors",
        mode === value
          ? "bg-background text-foreground"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
    </button>
  );

  return (
    <div
      className="not-prose my-4 overflow-hidden rounded-md border border-border bg-muted/40"
      data-testid="ascii-table-fence"
      data-mode={mode}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border/40 bg-muted/20 px-3 py-1">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">table</span>
        <div className="flex items-center gap-1.5">
          <div role="group" className="inline-flex rounded border border-border/60 bg-muted/40 p-0.5">
            {segment("rendered", t("outputs_view.fence_rendered"))}
            {segment("source", t("outputs_view.fence_source"))}
          </div>
          <button
            type="button"
            onClick={copy}
            className="rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            title={t("docs_content.copy_code")}
            aria-label={copied ? t("docs_content.code_copied") : t("docs_content.copy_code")}
          >
            {copied ? (
              <Check className="h-3 w-3 text-muted-foreground" aria-hidden="true" />
            ) : (
              <Copy className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
        </div>
      </div>
      {mode === "source" ? (
        <CodeBlock language="text" code={code} chrome={false} />
      ) : (
        <div className="overflow-x-auto bg-card">
          <table className="w-full border-collapse text-[13px] leading-snug">
            {grid.caption !== null && (
              <caption className="border-b border-border/40 bg-muted/20 px-3 py-1.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {grid.caption}
              </caption>
            )}
            {grid.head !== null && (
              <thead className="bg-muted/30">
                <tr>
                  {grid.head.map((cell, i) => (
                    <th
                      key={i}
                      scope="col"
                      className={cn(CELL, "text-left font-semibold text-foreground")}
                    >
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {grid.rows.map((row, ri) => (
                <tr key={ri} className="border-t border-border/60">
                  {row.length === 1 && grid.columns > 1 ? (
                    // A one-cell row inside a wider grid is a section banner in
                    // the original art — keep it spanning rather than dropping
                    // it into the first column.
                    <td
                      colSpan={grid.columns}
                      className={cn(CELL, "bg-muted/20 font-medium text-foreground")}
                    >
                      {row[0]}
                    </td>
                  ) : (
                    Array.from({ length: grid.columns }, (_, ci) => (
                      <td key={ci} className={CELL}>
                        {row[ci] ?? ""}
                      </td>
                    ))
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
