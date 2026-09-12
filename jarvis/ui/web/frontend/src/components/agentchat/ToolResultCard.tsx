import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";

interface ToolResult {
  ok: boolean;
  final_result: string;
  urls: string[];
  errors: string[];
}

/** Only recognize tool receipts, not arbitrary JSON examples or user data. */
export function parseToolResult(code: string): ToolResult | null {
  if (code.length > 100_000) return null;
  let value: unknown;
  try { value = JSON.parse(code); } catch { return null; } // Incomplete streamed JSON remains code.
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const result = value as Record<string, unknown>;
  if (typeof result.ok !== "boolean" || typeof result.final_result !== "string"
    || !Array.isArray(result.urls) || !Array.isArray(result.errors)) return null;
  if (!result.urls.every(url => typeof url === "string")
    || !result.errors.every(error => typeof error === "string")) return null;
  const errors = [...result.errors] as string[];
  if (typeof result.error === "string" && result.error.trim()) errors.push(result.error);
  return { ok: result.ok, final_result: result.final_result, urls: result.urls as string[], errors: [...new Set(errors)] };
}

function safeResultUrl(raw: string): boolean {
  try {
    const url = new URL(raw);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
  } catch { return false; } // Non-URL strings remain available in the raw receipt.
}

export function ToolResultCard({ result, code }: { result: ToolResult; code: string }) {
  const t = useT();
  const failed = !result.ok || result.errors.length > 0;
  const urls = [...new Set(result.urls)].filter(safeResultUrl);
  return <section className="not-prose my-3 min-w-0 rounded-lg border border-border bg-muted/30 p-3 text-sm" data-testid="tool-result-card">
    <p className="mb-2 font-medium text-foreground">{t("work_trace.output")} · {t(failed ? "work_trace.failed" : "work_trace.completed")}</p>
    {result.final_result.trim() ? <p className="whitespace-pre-wrap text-foreground [overflow-wrap:anywhere]">{result.final_result}</p> : null}
    {result.errors.length ? <ul className="mt-2 space-y-1 text-destructive">{result.errors.map((error, index) => <li key={index} className="whitespace-pre-wrap [overflow-wrap:anywhere]">{error}</li>)}</ul> : null}
    {urls.length ? <ul className="mt-2 space-y-1">{urls.map(url => <li key={url}><a href={url} className="text-primary underline [overflow-wrap:anywhere]" onClick={event => { event.preventDefault(); void openExternalUrl(url); }}>{url}</a></li>)}</ul> : null}
    <details className="mt-3 border-t border-border pt-2">
      <summary className="cursor-pointer text-xs text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("outputs_view.fence_source")} · JSON</summary>
      <pre tabIndex={0} className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground [overflow-wrap:anywhere]">{code}</pre>
    </details>
  </section>;
}
