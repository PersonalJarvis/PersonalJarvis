// The store's install standard — the CLI | runner | Prompt pattern every
// comparable store shows next to a listing.
//
// The strings arrive VERBATIM from the backend (jarvis/marketplace/
// install_standard.py). Nothing here builds a command out of a name: a store
// that derived its own strings client-side would drift from what the CLI
// actually accepts, and a shown-but-nonexistent command is the one bug a
// store is judged on.
//
// Shared by the Community browser (before installing) and the Publish tab
// (after publishing, where it is the line an author hands to everyone else),
// so both surfaces show the same three commands in the same order.
import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";

import { robustCopy } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

/** The three install surfaces, computed server-side. */
export interface InstallStandardWire {
  cli: string;
  runner: string;
  prompt: string;
}

// In the order a visitor tries them: the curated CLI command, the zero-install
// uvx runner, the assistant prompt.
const INSTALL_TABS = [
  { id: "cli", label: "CLI", hint: "Runs in any terminal while the app is running." },
  {
    id: "runner",
    label: "uvx",
    hint: "No install needed — uv fetches the CLI and runs the same command.",
  },
  {
    id: "prompt",
    label: "Prompt",
    hint: "Paste this to your assistant — it runs the install for you.",
  },
] as const;
type InstallTabId = (typeof INSTALL_TABS)[number]["id"];

export function InstallStandard({
  install,
  heading = "Install",
  note,
}: {
  install: InstallStandardWire;
  /** Overridden by the Publish tab, where the block is about sharing. */
  heading?: string;
  /** One extra line under the tabs' own hint, for surface-specific context. */
  note?: string;
}) {
  const [tab, setTab] = useState<InstallTabId>("cli");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  const active = INSTALL_TABS.find((t) => t.id === tab) ?? INSTALL_TABS[0];
  const value = install[tab];
  return (
    <section>
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-foreground">
          {heading}
        </h3>
        <div className="flex items-center">
          {INSTALL_TABS.map((t, i) => (
            <span key={t.id} className="flex items-center">
              {i > 0 && (
                <span aria-hidden className="mx-1.5 select-none text-muted-foreground/40">
                  |
                </span>
              )}
              <button
                type="button"
                onClick={() => {
                  setTab(t.id);
                  setCopied(false);
                }}
                className={cn(
                  "text-[11px] font-medium transition-colors",
                  tab === t.id ? "text-primary" : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t.label}
              </button>
            </span>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2 rounded-md border border-border bg-muted/40 py-1 pl-2.5 pr-1">
        <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-1 text-xs text-foreground">
          {tab !== "prompt" && (
            <span aria-hidden className="select-none text-muted-foreground">
              ${" "}
            </span>
          )}
          {value}
        </code>
        <button
          type="button"
          onClick={async () => {
            if (await robustCopy(value)) setCopied(true);
          }}
          className="grid h-7 w-7 shrink-0 place-items-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          title={copied ? "Copied" : "Copy"}
          aria-label={`Copy the ${active.label} install command`}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-primary" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
        </button>
      </div>
      <p className="mt-1.5 text-[11px] text-muted-foreground">{note ?? active.hint}</p>
    </section>
  );
}
