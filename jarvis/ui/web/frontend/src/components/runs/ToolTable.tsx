/**
 * Every tool / CLI this turn actually ran — with what it was given and what it
 * returned.
 *
 * `command` and `output` have been captured on the wire for a while (from
 * ToolCallStarted.args_preview / ToolCallCompleted.output_preview, both
 * redacted + length-capped by their publisher) but were never rendered, so the
 * table could only say "some tool ran, exit 0" — which is precisely the
 * question a developer does NOT have. They expand on click so the common case
 * stays a compact row.
 */
import { useState } from "react";
import { Check, ChevronDown, ChevronRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import type { ToolCall } from "./types";

/**
 * The risk ladder, read left to right: two quiet tiers, then the two that want
 * a human. `ask` is degraded (someone has to answer), `block` is a fault — and
 * the two quiet ones stay ink, because "safe" is not a status worth a colour.
 */
const RISK_INK: Record<string, string> = {
  safe: "text-foreground-faint",
  monitor: "text-muted-foreground",
  ask: "text-warning",
  block: "text-destructive",
};

export function ToolTable({ tools }: { tools: ToolCall[] }) {
  const t = useT();
  const [open, setOpen] = useState<Set<number>>(new Set());
  if (tools.length === 0) {
    return (
      <p className="text-base text-muted-foreground">{t("run_inspector.tools.empty")}</p>
    );
  }
  const toggle = (i: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  return (
    <ul className="space-y-0.5" data-testid="tool-table">
      {tools.map((tool, i) => {
        const detail = tool.command || tool.output || tool.error_line;
        const isOpen = open.has(i);
        const Chevron = isOpen ? ChevronDown : ChevronRight;
        return (
          <li key={`${tool.name}-${i}`} data-tool={tool.name} data-success={tool.success}>
            <button
              type="button"
              onClick={() => detail && toggle(i)}
              aria-expanded={detail ? isOpen : undefined}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                detail ? "hover:bg-secondary" : "cursor-default",
              )}
            >
              <span className="w-4 shrink-0 text-muted-foreground">
                {detail && <Chevron aria-hidden className="h-4 w-4" />}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-foreground">
                {tool.name}
              </span>
              {tool.caller && (
                <span className="shrink-0 truncate text-muted-foreground">
                  {tool.caller}
                </span>
              )}
              {tool.risk_tier && (
                <span
                  className={cn(
                    "shrink-0",
                    RISK_INK[tool.risk_tier] ?? "text-muted-foreground",
                  )}
                >
                  {tool.risk_tier}
                </span>
              )}
              {tool.approved_by && (
                <span className="flex shrink-0 items-center gap-1 text-muted-foreground">
                  <Check aria-hidden className="h-3.5 w-3.5" />
                  {tool.approved_by}
                </span>
              )}
              {tool.duration_ms != null && (
                <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
                  {tool.duration_ms}ms
                </span>
              )}
              <Badge variant={tool.success ? "success" : "destructive"} className="shrink-0">
                <span className="font-mono tabular-nums">
                  {tool.exit_code != null
                    ? `exit ${tool.exit_code}`
                    : tool.success
                      ? "ok"
                      : "fail"}
                </span>
              </Badge>
            </button>
            {isOpen && detail && (
              <div className="space-y-2 py-2 pl-8 pr-2">
                {tool.command && (
                  <Field label={t("run_inspector.tools.command")} value={tool.command} />
                )}
                {tool.output && (
                  <Field label={t("run_inspector.tools.output")} value={tool.output} />
                )}
                {tool.error_line && (
                  <Field
                    label={t("run_inspector.tools.error")}
                    value={tool.error_line}
                    tone="error"
                  />
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function Field({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "error";
}) {
  return (
    <div>
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <pre
        className={cn(
          "max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-secondary px-3 py-2 font-mono text-sm [overflow-wrap:anywhere]",
          tone === "error" ? "text-destructive" : "text-foreground",
        )}
      >
        {value}
      </pre>
    </div>
  );
}
