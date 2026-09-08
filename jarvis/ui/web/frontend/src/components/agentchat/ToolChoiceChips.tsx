import { Fragment, useState, type CSSProperties } from "react";
import { X } from "lucide-react";
import { useT } from "@/i18n";
import type { ToolChoice } from "./toolChoices";
import { splitMessageChips } from "./composerChips";
import { toolIdentity, toolIdentityStyle } from "./toolIdentity";
import "./toolIdentity.css";

export function ToolChoiceIcon({ row, size = 20 }: { row: ToolChoice; size?: number }) {
  const { logo, mark, Glyph } = toolIdentity(row);
  const [failedLogo, setFailedLogo] = useState<string | null>(null);
  return (
    <span
      className="tool-choice-icon"
      data-plate={mark === "plate"}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {logo && failedLogo !== logo ? (
        mark === "mono" ? (
          <span
            data-logo={logo}
            className="tool-choice-mask"
            style={{ "--tool-logo": `url("${logo}")` } as CSSProperties}
          />
        ) : (
          <img src={logo} alt="" onError={() => setFailedLogo(logo)} />
        )
      ) : (
        <Glyph width={size} height={size} strokeWidth={1.65} />
      )}
    </span>
  );
}

export function ToolChoiceChip({
  row,
  onRemove,
}: {
  row: ToolChoice;
  onRemove?: (id: string) => void;
}) {
  const t = useT();
  return (
    <span
      className="tool-identity tool-choice-chip"
      style={toolIdentityStyle(row)}
      data-brand={toolIdentity(row).key ?? row.category}
      data-tool-id={row.id}
      data-editable={Boolean(onRemove)}
      title={row.description || row.label}
    >
      <ToolChoiceIcon row={row} size={16} />
      <span className="truncate">{row.label}</span>
      {onRemove && (
        <button
          type="button"
          onClick={() => onRemove(row.id)}
          aria-label={`${t("chat_tools.remove")} ${row.label}`}
          className="ml-0.5 inline-flex h-5 w-5 items-center justify-center rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
        >
          <X className="h-3 w-3" aria-hidden />
        </button>
      )}
    </span>
  );
}

export function ToolChoiceChips({
  items,
  onRemove,
}: {
  items: ToolChoice[];
  onRemove?: (id: string) => void;
}) {
  if (!items.length) return null;
  return (
    <div
      className="tool-choice-list"
      data-editable={Boolean(onRemove)}
      data-testid="tool-choice-chips"
    >
      {items.map((row, index) => (
        <ToolChoiceChip key={`${row.id}:${index}`} row={row} onRemove={onRemove} />
      ))}
    </div>
  );
}

/** A stored sentence with `@gmail` tags drawn as brand pills in the same line. */
export function MessageWithChips({ text, choices }: { text: string; choices: ToolChoice[] }) {
  const parts = splitMessageChips(text, choices);
  if (!parts.length) return null;
  return (
    <span data-testid="tool-choice-chips" data-editable="false" className="whitespace-pre-wrap">
      {parts.map((part, index) =>
        part.type === "text" ? (
          <Fragment key={index}>{part.text}</Fragment>
        ) : (
          <ToolChoiceChip key={`${part.row.id}:${index}`} row={part.row} />
        ),
      )}
    </span>
  );
}
