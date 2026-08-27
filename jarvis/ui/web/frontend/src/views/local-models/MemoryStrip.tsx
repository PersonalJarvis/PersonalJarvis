/**
 * What sits in graphics memory when every job is loaded at once.
 *
 * The card's own bar answers "does THIS model fit"; this strip answers the
 * question that decides a local setup — whether the chat model, the voice
 * brain and the embedder fit TOGETHER, because a call arrives while a chat
 * is loaded. The backend adds it up (`roles.resident`: one segment per
 * distinct download, its context estimate, the speech stack's reserve); the
 * strip only draws it, with the total against the card and one sentence
 * when it does not fit — Ollama then swaps, and the user hears the pause.
 */
import type { LocalModelRole, ResidentPayload } from "@/hooks/useLocalModels";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { formatGb } from "./localModelsFormat";

export interface MemoryStripProps {
  resident: ResidentPayload | undefined;
  roleLabel: (role: LocalModelRole) => string;
}

const GIB = 1024 ** 3;

export function MemoryStrip({ resident, roleLabel }: MemoryStripProps) {
  const t = useT();
  if (!resident || resident.items.length === 0) return null;
  const k = (key: string) => t(`local_models.overview.${key}`);
  const budget = resident.accelerator_gb;
  const total = resident.total_gb;
  const scale = budget > 0 ? Math.max(budget, total) : total || 1;
  const width = (gb: number) => `${Math.max(0, (gb / scale) * 100)}%`;
  const gb = (value: number) => formatGb(value * GIB);

  const headline =
    budget > 0
      ? fill(k(resident.over ? "resident_over" : "resident_fits"), {
          total: gb(total),
          gb: budget.toFixed(1),
        })
      : fill(k("resident_unknown"), { total: gb(total) });

  return (
    <section
      className="rounded-2xl border border-border bg-card/40 px-4 py-3"
      data-testid="memory-strip"
      data-over={resident.over ? "true" : "false"}
      aria-label={k("resident_title")}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-xs text-muted-foreground">{k("resident_title")}</p>
        <p
          className={cn(
            "text-xs font-medium tabular-nums",
            resident.over ? "text-amber-700 dark:text-amber-400" : "text-foreground",
          )}
          data-testid="memory-strip-total"
        >
          {headline}
        </p>
      </div>
      <div
        className="mt-2 flex h-2.5 overflow-hidden rounded-full bg-sheen/[0.08]"
        role="img"
        aria-label={headline}
      >
        {resident.items.map((item, i) => (
          <span key={item.tag} className="contents">
            <span
              className={cn("h-full bg-primary", i % 2 === 1 && "opacity-75")}
              style={{ width: width(item.weights_gb) }}
              title={`${item.display_label || item.tag} · ${gb(item.weights_gb)}`}
            />
            <span
              className="h-full bg-primary/40"
              style={{ width: width(item.context_gb) }}
              title={`${k("resident_context")} · ${gb(item.context_gb)}`}
            />
          </span>
        ))}
        {resident.reserve_gb > 0 && (
          <span
            className="h-full bg-sheen/25"
            style={{ width: width(resident.reserve_gb) }}
            title={`${k("resident_reserve")} · ${gb(resident.reserve_gb)}`}
          />
        )}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        {resident.items.map((item) => (
          <li key={item.tag} className="flex items-center gap-1.5" data-testid={`memory-strip-${item.tag}`}>
            <i className="inline-block h-2 w-2 rounded-[2px] bg-primary" aria-hidden />
            <span className="text-foreground/90">{item.display_label || item.tag}</span>
            <span>
              {item.roles.map(roleLabel).join(" · ")} · {gb(item.weights_gb)}
              {item.context_gb > 0 ? ` + ${gb(item.context_gb)} ${k("resident_context_short")}` : ""}
              {item.loaded ? ` · ${t("local_models.installed.loaded")}` : ""}
            </span>
          </li>
        ))}
        {resident.reserve_gb > 0 && (
          <li className="flex items-center gap-1.5">
            <i className="inline-block h-2 w-2 rounded-[2px] bg-sheen/25" aria-hidden />
            <span>
              {k("resident_reserve")} · {gb(resident.reserve_gb)}
            </span>
          </li>
        )}
      </ul>
    </section>
  );
}
