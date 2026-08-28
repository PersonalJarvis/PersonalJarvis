/**
 * The install commands for one marketplace listing, drawn as a small terminal.
 *
 * The same block the community registry's README shows above its listings
 * (`src/components/marketplace/InstallTerminal.astro` over there), so a user
 * who saw a command on the website recognises it here — one install language
 * across both surfaces.
 *
 * A listing can carry more than one standard: `jarvis marketplace install …`
 * for this app, and `npx skills add …` for every other agent that reads
 * SKILL.md files (see `installStandard.ts`). Each gets a tab, because they are
 * alternatives — running both installs the same skill twice, in two places.
 * With a single command the tab strip stays out of the way entirely.
 *
 * One deliberate difference from the website's copy: no OS switch. The app
 * runs where the command would run, so it draws the prompt this machine
 * actually uses instead of asking.
 *
 * Colours come from theme tokens, never a hardcoded mode: on a dark
 * appearance this reads as a terminal, on a light one as a code block with a
 * window bar, and both are the app's own palette.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

import { robustCopy } from "@/lib/clipboard";
import { shellPrompt, type InstallCommand } from "@/lib/installStandard";
import { cn } from "@/lib/utils";

export function InstallTerminal({
  commands,
  path,
  comment,
  className,
}: {
  /** The install standards this listing supports, in the order shown. */
  commands: InstallCommand[];
  /** What the window bar shows as the working directory. */
  path: string;
  /** The dimmed line above the command. */
  comment?: string;
  className?: string;
}) {
  const [activeId, setActiveId] = useState(commands[0]?.id);
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | undefined>(undefined);

  // A pending copy must never outlive the dialog it was started from.
  useEffect(() => () => window.clearTimeout(resetTimer.current), []);

  const active = commands.find((c) => c.id === activeId) ?? commands[0];

  const onCopy = useCallback(async () => {
    if (!active) return;
    const ok = await robustCopy(active.command);
    if (!ok) return;
    setCopied(true);
    window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setCopied(false), 2000);
  }, [active]);

  if (!active) return null;

  return (
    <div className={className}>
      {commands.length > 1 && (
        <div role="tablist" aria-label="Install method" className="mb-1.5 flex gap-1">
          {commands.map((cmd) => (
            <button
              key={cmd.id}
              type="button"
              role="tab"
              aria-selected={cmd.id === active.id}
              onClick={() => {
                setActiveId(cmd.id);
                // The confirmation belongs to the line that was copied; leaving
                // it up after a switch would claim the new line is on the
                // clipboard when it is not.
                window.clearTimeout(resetTimer.current);
                setCopied(false);
              }}
              className={cn(
                "rounded-md border px-2 py-1 font-mono text-[11px] transition-colors",
                cmd.id === active.id
                  ? "border-accent bg-accent/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {cmd.label}
            </button>
          ))}
        </div>
      )}

      <div className="relative overflow-hidden rounded-xl border border-border bg-card/60">
        {/* The hairline of accent light along the top edge — the same signal
            cue the website's terminals carry. */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent"
        />

        <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-3 py-2">
          <span aria-hidden className="flex shrink-0 gap-1.5">
            <i className="h-2 w-2 rounded-full bg-accent/70" />
            <i className="h-2 w-2 rounded-full bg-muted-foreground/30" />
            <i className="h-2 w-2 rounded-full bg-muted-foreground/30" />
          </span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            {path}
          </span>
          <button
            type="button"
            onClick={onCopy}
            aria-label="Copy the install command"
            className={cn(
              "ml-auto inline-flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[11px] transition-colors",
              copied
                ? "border-accent bg-accent text-accent-foreground"
                : "border-border text-muted-foreground hover:text-foreground",
            )}
          >
            {copied ? (
              <Check className="h-3 w-3" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>

        <div className="px-3 py-2.5">
          {comment && (
            <p className="install-cmd text-[12px] text-muted-foreground">
              {comment}
            </p>
          )}
          <p className="install-cmd text-[12.5px] text-foreground">
            <span className="mr-1.5 select-none font-semibold text-accent">
              {shellPrompt()}
            </span>
            {active.command}
            <span aria-hidden className="install-caret" />
          </p>
        </div>
      </div>
      {active.note && (
        <p className="mt-1.5 text-[11px] text-muted-foreground">{active.note}</p>
      )}
    </div>
  );
}
