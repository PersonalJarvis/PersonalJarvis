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
import { ChevronDown, ChevronRight } from "lucide-react";

import { useT } from "@/i18n";

import type { ToolCall } from "./types";

/**
 * The risk ladder, read left to right: two quiet tiers, then the two that want
 * a human. `ask` is degraded (someone has to answer), `block` is a fault. The
 * old table painted `monitor` sky and `ask` in near-white, which made the
 * second-quietest tier the loudest thing in the row.
 */
const RISK_STYLE: Record<string, string> = {
  safe: "text-faint-foreground",
  monitor: "text-muted-foreground",
  ask: "text-warning",
  block: "text-destructive",
};

export function ToolTable({ tools }: { tools: ToolCall[] }) {
  const t = useT();
  const [open, setOpen] = useState<Set<number>>(new Set());
  if (tools.length === 0) {
    return (
      <span className="text-body text-muted-foreground">
        {t("run_inspector.tools.empty")}
      </span>
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
    <ul className="space-y-1" data-testid="tool-table">
      {tools.map((tool, i) => {
        const detail = tool.command || tool.output || tool.error_line;
        const isOpen = open.has(i);
        return (
          <li
            key={`${tool.name}-${i}`}
            data-tool={tool.name}
            data-success={tool.success}
            className="overflow-hidden rounded-md"
          >
            <button
              type="button"
              onClick={() => detail && toggle(i)}
              className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-micro transition-colors ${
                detail ? "hover:bg-secondary" : "cursor-default"
              }`}
            >
              <span className="w-3 shrink-0 text-muted-foreground">
                {detail ? (
                  isOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )
                ) : null}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-foreground">
                {tool.name}
              </span>
              {tool.caller && (
                <span className="shrink-0 text-muted-foreground">{tool.caller}</span>
              )}
              {tool.risk_tier && (
                <span
                  className={`shrink-0 ${RISK_STYLE[tool.risk_tier] ?? "text-muted-foreground"}`}
                >
                  {tool.risk_tier}
                </span>
              )}
              {tool.approved_by && (
                <span className="shrink-0 text-muted-foreground">
                  ✓ {tool.approved_by}
                </span>
              )}
              {tool.duration_ms != null && (
                <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
                  {tool.duration_ms}ms
                </span>
              )}
              <span
                className={`shrink-0 font-mono tabular-nums ${
                  tool.success ? "text-success" : "text-destructive"
                }`}
              >
                {tool.exit_code != null
                  ? `exit ${tool.exit_code}`
                  : tool.success
                    ? "ok"
                    : "fail"}
              </span>
            </button>
            {isOpen && detail && (
              <div className="space-y-stack px-3 pb-2 pt-1.5">
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
      <div className="mb-1 text-micro text-muted-foreground">{label}</div>
      <pre
        className={`overflow-x-auto whitespace-pre-wrap break-words rounded-md bg-secondary px-2 py-1.5 font-mono text-micro [overflow-wrap:anywhere] ${
          tone === "error" ? "text-destructive" : "text-foreground"
        }`}
      >
        {value}
      </pre>
    </div>
  );
}
