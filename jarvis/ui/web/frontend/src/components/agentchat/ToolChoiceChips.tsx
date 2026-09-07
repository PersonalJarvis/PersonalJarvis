import { Brain, Globe, Paperclip, Plug, Sparkles, Terminal, Wrench, X } from "lucide-react";
import { McpLogo } from "@/components/extensions/McpLogo";
import { useT } from "@/i18n";
import type { ToolChoice } from "./toolChoices";

// Reuse the original offline brand marks and their existing provenance ledger.
const logos = import.meta.glob("../../assets/brands/*.svg", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

export function ToolChoiceIcon({ row }: { row: ToolChoice }) {
  const brand = row.brand || (row.category === "mcp" ? row.group.replace(/[-_]mcp$/, "") : "");
  const logo = logos[`../../assets/brands/${brand}.svg`];
  if (logo)
    return (
      <img
        src={logo}
        alt=""
        className="h-6 w-6 shrink-0 rounded bg-foreground/80 p-1 object-contain dark:bg-secondary"
      />
    );
  const Icon =
    row.category === "mcp"
      ? McpLogo
      : row.category === "memory"
        ? Brain
        : row.category === "skills"
          ? Sparkles
          : row.category === "plugins"
            ? Plug
            : row.category === "web"
              ? Globe
              : row.category === "files"
                ? Paperclip
                : row.category === "cli"
                  ? Terminal
                  : Wrench;
  return <Icon aria-hidden className="h-5 w-5 shrink-0 text-muted-foreground" />;
}

export function ToolChoiceChips({
  items,
  onRemove,
}: {
  items: ToolChoice[];
  onRemove?: (id: string) => void;
}) {
  const t = useT();
  if (!items.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5" data-testid="tool-choice-chips">
      {items.map((row) => (
        <span
          key={row.id}
          className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-border-strong bg-secondary px-2 py-1 text-xs text-foreground"
        >
          <ToolChoiceIcon row={row} />
          <span className="truncate">{row.label}</span>
          {onRemove && (
            <button
              type="button"
              onClick={() => onRemove(row.id)}
              aria-label={`${t("chat_tools.remove")} ${row.label}`}
              className="rounded p-0.5 hover:bg-muted focus-visible:ring-2 focus-visible:ring-primary"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </span>
      ))}
    </div>
  );
}
