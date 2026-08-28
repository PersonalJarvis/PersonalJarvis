import { Blocks, ClipboardList } from "lucide-react";
import type { ComponentType, SVGProps } from "react";
import { McpLogo } from "@/components/extensions/McpLogo";
import { useEventStore, type SectionId } from "@/store/events";
import { usePluginAttention } from "@/hooks/usePluginAttention";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { SkillsView } from "@/views/SkillsView";
import { PluginsView } from "@/views/PluginsView";
import { McpsView } from "@/views/McpsView";

/**
 * Combined "Skills, Plugins & MCPs" section.
 *
 * One settings-style surface: a narrow navigation column on the left (the
 * three areas under one "Customize" heading) and the chosen area on the right.
 * Each area is a quiet table — name, a couple of facts, an on/off switch — and
 * opens a detail page in place when a row is clicked.
 *
 * Design note — why this is a thin wrapper, not a rewrite:
 * The active sidebar section id (`activeSection` in the event store) *is* the
 * navigation state. We deliberately keep the section ids (`skills`, `plugins`,
 * `mcps`) alive in the five-layer nav enum — see `jarvis/plugins/tool/navigate.py`
 * + `store/events.ts` + the parity test `tests/unit/plugins/tool/test_navigate.py`.
 * Only the *presentation* collapses to one entry; routing, deep-links and voice
 * navigation ("öffne Plugins") keep working unchanged and land on the i18n-allow
 * right area. The child views rehydrate their own state from React Query / the
 * store, so the unmount/remount on a switch is harmless.
 *
 * The file/component name stays `ExtensionsView` for continuity.
 */

interface AreaSpec {
  id: SectionId;
  labelKey: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

const AREAS = [
  // A written-down, repeatable procedure — a sheet with the steps on it. Not
  // the scroll glyph another assistant vendor uses for its skills feature, not
  // a graduation cap (reads as a school app) and not a bolt (reads as an
  // action, while a skill is a routine).
  { id: "skills", labelKey: "nav.skills", icon: ClipboardList },
  { id: "plugins", labelKey: "nav.plugins", icon: Blocks },
  // The official Model Context Protocol mark, not a generic plug.
  { id: "mcps", labelKey: "nav.mcps", icon: McpLogo },
] as const satisfies readonly AreaSpec[];

type AreaId = (typeof AREAS)[number]["id"];

export function ExtensionsView() {
  const t = useT();
  const active = useEventStore((s) => s.activeSection);
  const setActive = useEventStore((s) => s.setActiveSection);
  const attention = usePluginAttention();

  // The router only mounts us for skills/plugins/mcps; any other value is
  // unexpected — fall back to Skills defensively.
  const current: AreaId = AREAS.some((a) => a.id === active) ? (active as AreaId) : "skills";

  return (
    <div className="flex h-full min-h-0">
      <nav
        aria-label={t("extensions.group_label")}
        className="flex w-[200px] shrink-0 flex-col border-r border-border px-3 py-5"
      >
        <p className="px-2.5 pb-2 text-[11px] font-medium text-muted-foreground">
          {t("extensions.group_label")}
        </p>
        <ul className="space-y-0.5">
          {AREAS.map((area) => {
            const Icon = area.icon;
            const isActive = area.id === current;
            const dot = area.id === "plugins" && attention.count > 0;
            return (
              <li key={area.id}>
                <button
                  type="button"
                  onClick={() => setActive(area.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                    isActive
                      ? "bg-sheen/[0.08] font-medium text-foreground"
                      : "text-foreground/75 hover:bg-sheen/[0.05] hover:text-foreground",
                  )}
                >
                  <Icon className={cn("h-4 w-4 shrink-0", isActive ? "text-foreground" : "text-muted-foreground")} />
                  <span className="flex-1 truncate">{t(area.labelKey)}</span>
                  {dot && (
                    <span
                      aria-label={t("extensions.attention_dot")}
                      title={t("extensions.attention_dot")}
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-foreground"
                    />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="min-h-0 min-w-0 flex-1">
        {current === "skills" && <SkillsView />}
        {current === "plugins" && <PluginsView />}
        {current === "mcps" && <McpsView />}
      </div>
    </div>
  );
}
