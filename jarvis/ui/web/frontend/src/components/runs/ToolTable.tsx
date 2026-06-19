import type { ToolCall } from "./types";

export function ToolTable({ tools }: { tools: ToolCall[] }) {
  if (tools.length === 0) return <span className="text-muted-foreground/60">—</span>;
  return (
    <table className="w-full text-[11px]">
      <tbody>
        {tools.map((t, i) => (
          <tr key={i} className="border-t border-border/40 align-top">
            <td className="py-0.5 font-mono">
              {t.name}
              {t.error_line && (
                <div className="max-w-[420px] truncate font-sans text-[10px] text-destructive/80">
                  {t.error_line}
                </div>
              )}
            </td>
            <td className="text-muted-foreground">{t.risk_tier || "—"}</td>
            <td className="text-muted-foreground">{t.approved_by ?? ""}</td>
            <td className="text-right">{t.duration_ms != null ? `${t.duration_ms}ms` : ""}</td>
            <td className={`text-right ${t.success ? "text-emerald-500" : "text-destructive"}`}>
              {t.exit_code != null ? `exit ${t.exit_code}` : (t.success ? "ok" : "fail")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
