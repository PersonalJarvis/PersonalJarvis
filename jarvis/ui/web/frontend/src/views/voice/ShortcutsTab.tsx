import { Keyboard } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";

export interface ShortcutsTabProps {
  /**
   * Suppress this view's own `ViewHeader`.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second bordered band right below it reads as a
   * rendering fault. Standalone rendering keeps its own header.
   */
  hideHeader?: boolean;
}

/**
 * "Shortcuts" tab of the merged voice section — the keys that start dictation.
 *
 * Placeholder shell: the shell, its section id and its i18n keys ship first so
 * the rest of the section can be built against a stable surface. The two
 * keybind rows (push-to-talk + hands-free) and the on-screen keyboard land here
 * next.
 */
export function ShortcutsTab({ hideHeader = false }: ShortcutsTabProps = {}) {
  const t = useT();

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Keyboard className="h-4 w-4 text-primary" />}
          title={t("voice.shortcuts.title")}
          subtitle={t("voice.shortcuts.description")}
        />
      )}
      <div
        className="flex-1 overflow-y-auto scrollbar-jarvis p-6"
        data-testid="voice-shortcuts-tab"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4" />
      </div>
    </div>
  );
}
