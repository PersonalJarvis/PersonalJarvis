import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, Sparkles, Terminal, Wand2 } from "lucide-react";
import {
  fetchPromptWriter,
  savePromptWriter,
  type PromptWriterOption,
  type PromptWriterState,
} from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * Who writes Agentic-IDE task briefs: the Tool Model, or a connected coding CLI.
 *
 * The choice exists because the two are billed and sourced completely
 * differently — a coding CLI runs against a subscription the user already pays
 * for, the Tool Model against an API key billed per token — and until now the
 * decision was made for them by a resolver order they could not see. A user who
 * had deliberately pinned a strong Tool Model still found their briefs written
 * by whichever CLI happened to be signed in.
 *
 * Two properties this card has to keep:
 *
 * * **It names the real thing.** Every label comes from the server, which reads
 *   it off the provider card — so the CLI shown is the one this install
 *   actually connected, and the Tool Model row carries the model the user
 *   picked. Nothing here hardcodes a vendor; an install with a CLI this file
 *   has never heard of lists it correctly.
 * * **An option that cannot run is disabled, not hidden.** "Why is my
 *   subscription not an option" is the question this screen exists to answer,
 *   and a silently absent row answers nothing.
 */
export function PromptWriterCard() {
  const t = useT();
  const [state, setState] = useState<PromptWriterState | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  // Two separate failures, because they have different lifetimes. The load
  // error is a property of the current data; the save error is a message from
  // the server about the last thing the user tried, and it has to survive the
  // re-renders that follow. Holding both in one string let a re-render's
  // reload wipe the save error before it could be read.
  const [loadFailed, setLoadFailed] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [loading, setLoading] = useState(true);

  // Deliberately no dependencies: this must run once per mount. Depending on
  // `t` re-ran it on every render, which reset the state mid-interaction.
  const load = useCallback(async () => {
    try {
      setState(await fetchPromptWriter());
      setLoadFailed(false);
    } catch {
      // A settings card that cannot read its own state says so rather than
      // rendering an empty picker that looks like "no options exist".
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const choose = async (id: string) => {
    if (pending || state?.prompt_writer === id) return;
    setPending(id);
    setSaveError("");
    try {
      setState(await savePromptWriter(id));
    } catch (err) {
      // The server's own message names the blocker — an unsigned CLI, a Tool
      // Model with no key. Showing ours instead would send the user to fix the
      // wrong thing.
      setSaveError(
        err instanceof Error && err.message
          ? err.message
          : t("prompt_writer.save_failed"),
      );
    } finally {
      setPending(null);
    }
  };

  const error = saveError || (loadFailed ? t("prompt_writer.unavailable") : "");

  if (loading) {
    return (
      <div className="card-outline flex items-center gap-2 p-4 text-[11px] text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {t("prompt_writer.loading")}
      </div>
    );
  }

  const options = state?.options ?? [];
  const modes = options.filter((o) => MODE_IDS.has(o.id));
  const clis = options.filter((o) => !MODE_IDS.has(o.id));

  return (
    <div className="card-outline space-y-3 p-4" data-testid="prompt-writer-card">
      <div className="space-y-1">
        <h4 className="text-[12px] font-medium">{t("prompt_writer.title")}</h4>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("prompt_writer.description")}
        </p>
      </div>

      <div className="space-y-2">
        {modes.map((option) => (
          <WriterRow
            key={option.id}
            option={option}
            selected={state?.prompt_writer === option.id}
            pending={pending === option.id}
            disabled={pending !== null}
            onSelect={choose}
          />
        ))}
      </div>

      {clis.length > 0 && (
        <div className="space-y-2">
          <p className="px-1 text-[10px] uppercase tracking-wider text-muted-foreground">
            {t("prompt_writer.connected_clis")}
          </p>
          {clis.map((option) => (
            <WriterRow
              key={option.id}
              option={option}
              selected={state?.prompt_writer === option.id}
              pending={pending === option.id}
              disabled={pending !== null}
              onSelect={choose}
            />
          ))}
        </div>
      )}

      {error && (
        <p role="alert" className="text-[11px] leading-relaxed text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** The four selection MODES, as opposed to a concrete connected CLI. */
const MODE_IDS = new Set(["auto", "tool_model", "subscription", "api"]);

function iconFor(id: string) {
  if (id === "auto") return Wand2;
  if (id === "tool_model" || id === "api") return Sparkles;
  return Terminal;
}

function WriterRow({
  option,
  selected,
  pending,
  disabled,
  onSelect,
}: {
  option: PromptWriterOption;
  selected: boolean;
  pending: boolean;
  disabled: boolean;
  onSelect: (id: string) => void;
}) {
  const t = useT();
  const Icon = iconFor(option.id);
  // An unusable option stays visible but unselectable — except when it is
  // already the persisted choice, which the user needs to be able to see and
  // move away from.
  const blocked = !option.connected && !selected;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      disabled={blocked || disabled}
      onClick={() => onSelect(option.id)}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg border px-3 py-2 text-left transition",
        selected
          ? "border-primary/60 bg-primary/5"
          : "border-border hover:border-primary/40",
        blocked && "cursor-not-allowed opacity-50 hover:border-border",
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 text-[11px] leading-snug">
        <span className="block truncate">{option.label}</span>
        {blocked && (
          <span className="block text-[10px] text-muted-foreground">
            {option.id === "tool_model"
              ? t("prompt_writer.tool_model_unset")
              : t("prompt_writer.not_connected")}
          </span>
        )}
      </span>
      {pending ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
      ) : (
        selected && <Check className="h-3.5 w-3.5 shrink-0 text-primary" />
      )}
    </button>
  );
}
