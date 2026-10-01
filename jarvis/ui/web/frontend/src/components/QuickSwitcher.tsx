/**
 * The quick switcher — Ctrl+Space anywhere, type where you want to go, Enter.
 *
 * Modelled on the Mac's Spotlight bar: one large field floating high on the
 * screen over a frosted panel, results directly underneath with the first one
 * already selected, the app still visible behind it (no dimming scrim). What it
 * lists and how it ranks lives in `@/lib/quickSwitch`; this file only draws it.
 *
 * cmdk drives the keyboard (arrows, Enter, the active row) with its own
 * filtering switched OFF — the ranking is ours and must not be re-scored
 * (the same reason WikiSearch turns it off). Radix Dialog owns Escape, the
 * focus trap and handing focus back to the pane or field you came from.
 */
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { CornerDownLeft, Search, Settings2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useSettingsJump } from "@/store/settingsJump";
import {
  ambiguousEntryKeys,
  rankQuickSwitch,
  strongSettingsMatches,
  type QuickSwitchEntry,
} from "@/lib/quickSwitch";
import { searchSettingsOptions } from "@/views/settings/settingsSearch";
import { cn } from "@/lib/utils";

/** Resolve a label, falling back when the key is missing (the resolver echoes it). */
function useLabel() {
  const t = useT();
  return (key: string, fallback: string) => {
    const value = t(key);
    return value === key ? fallback : value;
  };
}

const ROW = cn(
  "group flex h-11 cursor-default select-none items-center gap-3 rounded-lg px-2.5",
  "text-base text-foreground outline-none",
  "data-[selected=true]:bg-accent data-[selected=true]:text-accent-foreground",
);

const TILE = cn(
  "grid h-7 w-7 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground",
  "group-data-[selected=true]:bg-white/20 group-data-[selected=true]:text-accent-foreground",
);

const DETAIL = cn(
  "ml-auto shrink-0 truncate pl-3 text-sm text-muted-foreground",
  "group-data-[selected=true]:text-accent-foreground/80",
);

export function QuickSwitcher({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const t = useT();
  const label = useLabel();
  const language = useUiLanguage();
  const activeSection = useEventStore((s) => s.activeSection);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const setSurface = useHomeStore((s) => s.setSurface);
  const requestSettingsJump = useSettingsJump((s) => s.request);
  const [query, setQuery] = useState("");

  // About forty destinations — ranked inline on every keystroke, no memo needed.
  const labelFor = (item: QuickSwitchEntry) => label(item.labelKey, item.fallbackLabel);
  const ranked = rankQuickSwitch(query, labelFor);
  const ambiguous = ambiguousEntryKeys(labelFor);
  const settingsMatches = useMemo(
    () => strongSettingsMatches(query, searchSettingsOptions(language, query, t)),
    [query, language, t],
  );

  const close = () => {
    onOpenChange(false);
    setQuery("");
  };

  const go = (item: QuickSwitchEntry) => {
    if (item.surface) setSurface(item.surface);
    setActiveSection(item.section);
    close();
  };

  const goToSetting = (groupId: string) => {
    requestSettingsJump(groupId);
    setActiveSection("settings");
    close();
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) setQuery("");
        onOpenChange(next);
      }}
    >
      <Dialog.Portal>
        {/* Spotlight never dims the desktop: the overlay only catches the
            click that closes it. */}
        <Dialog.Overlay className="fixed inset-0 z-[80]" />
        <Dialog.Content
          data-testid="quick-switcher"
          aria-describedby={undefined}
          className={cn(
            "fixed left-1/2 top-[18%] z-[90] w-[min(640px,calc(100vw-2rem))] -translate-x-1/2",
            "overflow-hidden rounded-2xl border border-border shadow-float",
            "backdrop-blur-2xl backdrop-saturate-150",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95",
            "motion-reduce:animate-none",
          )}
          style={{ backgroundColor: "hsl(var(--popover) / 0.86)" }}
        >
          <Dialog.Title className="sr-only">{t("quick_switch.title")}</Dialog.Title>
          <Command shouldFilter={false} loop label={t("quick_switch.title")}>
            <div className="flex items-center gap-3 px-4">
              <Search className="h-6 w-6 shrink-0 text-muted-foreground" aria-hidden />
              <Command.Input
                autoFocus
                value={query}
                onValueChange={setQuery}
                placeholder={t("quick_switch.placeholder")}
                data-testid="quick-switcher-input"
                className="h-14 flex-1 bg-transparent text-2xl font-light text-foreground outline-none placeholder:text-muted-foreground"
              />
            </div>
            <Command.List className="max-h-[min(26rem,60dvh)] overflow-y-auto border-t border-border p-1.5 scrollbar-jarvis">
              <Command.Empty className="px-3 py-6 text-center text-base text-muted-foreground">
                {t("quick_switch.no_results")}
              </Command.Empty>
              {ranked.length > 0 && (
                <Command.Group
                  heading={t("quick_switch.group_sections")}
                  className="[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground"
                >
                  {ranked.map(({ entry: item, label: text }) => {
                    const Icon = item.icon;
                    const here = item.section === activeSection && !item.surface;
                    const area = item.parentLabelKey ? label(item.parentLabelKey, "") : "";
                    // A label two rows share names its area up front, e.g.
                    // "Voice › API Keys" beside the API Keys page itself.
                    const shown = area && ambiguous.has(item.key) ? `${area} › ${text}` : text;
                    return (
                      <Command.Item
                        key={item.key}
                        value={`section:${item.key}`}
                        onSelect={() => go(item)}
                        data-testid={`quick-switch-${item.key}`}
                        className={ROW}
                      >
                        <span className={TILE}>
                          <Icon className="h-4 w-4" aria-hidden />
                        </span>
                        <span className="min-w-0 truncate">{shown}</span>
                        <span className={DETAIL}>
                          {here ? t("quick_switch.current") : shown === text ? area : ""}
                        </span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              )}
              {settingsMatches.length > 0 && (
                <Command.Group
                  heading={t("quick_switch.group_settings")}
                  className="[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground"
                >
                  {settingsMatches.map((match) => (
                    <Command.Item
                      key={match.id}
                      value={`setting:${match.id}`}
                      onSelect={() => goToSetting(match.id)}
                      data-testid={`quick-switch-setting-${match.id}`}
                      className={ROW}
                    >
                      <span className={TILE}>
                        <Settings2 className="h-4 w-4" aria-hidden />
                      </span>
                      <span className="min-w-0 truncate">{match.label}</span>
                      <span className={DETAIL}>{label("nav.settings", "Settings")}</span>
                    </Command.Item>
                  ))}
                </Command.Group>
              )}
            </Command.List>
            <div className="flex items-center justify-end gap-4 border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1.5">
                <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">
                  <CornerDownLeft className="h-3 w-3" aria-hidden />
                </kbd>
                {t("quick_switch.hint_open")}
              </span>
              <span className="inline-flex items-center gap-1.5">
                <kbd className="inline-flex h-5 items-center rounded border border-border px-1 font-sans">esc</kbd>
                {t("quick_switch.hint_close")}
              </span>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
