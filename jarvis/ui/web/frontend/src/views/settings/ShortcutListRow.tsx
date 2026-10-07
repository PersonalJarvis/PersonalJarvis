/**
 * One row of the Keyboard shortcuts list: what the shortcut does and where it
 * works on the left, its keys and the controls that change it on the right.
 *
 * Every row on that page — fixed chords, voice keys, the quick switcher, the
 * zoom steps, the appshot keys — is drawn by this component, so the keys line
 * up in one column however a row is edited. The control column keeps its width
 * when a row has no controls, for the same reason.
 */
import { Fragment, type ReactNode } from "react";
import { AppWindow, Globe, SquareTerminal } from "lucide-react";

import { useT } from "@/i18n";
import type { ShortcutScope } from "@/lib/shortcutRegistry";

const SCOPE: Record<ShortcutScope, { icon: typeof Globe; labelKey: string }> = {
  global: { icon: Globe, labelKey: "shortcuts_view.scope_global" },
  window: { icon: AppWindow, labelKey: "shortcuts_view.scope_window" },
  terminal: { icon: SquareTerminal, labelKey: "shortcuts_view.scope_terminal" },
};

/** Keycaps for labels that are already display text ("Ctrl", "Space", "⌥"). */
export function KeyCaps({ caps }: { caps: string[] }) {
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-1">
      {caps.map((cap, i) => (
        <Fragment key={`${cap}-${i}`}>
          {i > 0 && <span className="text-muted-foreground/50">+</span>}
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-micro text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.35)]">
            {cap}
          </kbd>
        </Fragment>
      ))}
    </span>
  );
}

/** The quiet text a row shows instead of keys ("off", "not assigned", "—"). */
export function NoKeys({ children }: { children: ReactNode }) {
  return <span className="text-sm italic text-muted-foreground">{children}</span>;
}

export function ShortcutListRow({
  title,
  scope,
  chord,
  actions,
  children,
  testId,
  recording = false,
}: {
  title: string;
  scope: ShortcutScope;
  chord: ReactNode;
  actions?: ReactNode;
  /** Shown under the row: an editor, an error, a reset link. */
  children?: ReactNode;
  testId?: string;
  /** Marks the row while it records, so app-level chords stand down. */
  recording?: boolean;
}) {
  const t = useT();
  const { icon: ScopeIcon, labelKey } = SCOPE[scope];
  return (
    <li
      data-testid={testId}
      data-keybind-recording={recording ? "true" : undefined}
      className="px-5 py-3"
    >
      <div className="flex items-center gap-4">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-foreground">{title}</p>
          <p className="mt-1 flex items-center gap-1.5 text-micro text-muted-foreground">
            <ScopeIcon className="h-3 w-3 shrink-0" aria-hidden />
            {t(labelKey)}
          </p>
        </div>
        <div className="flex shrink-0 items-center justify-end">{chord}</div>
        <div className="flex w-[4.5rem] shrink-0 items-center justify-end gap-0.5">{actions}</div>
      </div>
      {children}
    </li>
  );
}
