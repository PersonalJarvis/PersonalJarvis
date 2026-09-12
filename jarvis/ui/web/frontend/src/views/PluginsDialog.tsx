import * as Dialog from "@radix-ui/react-dialog";
import { useRef } from "react";
import { X } from "lucide-react";
import { useT } from "@/i18n";
import { PluginsView } from "@/views/PluginsView";
import { SkillsView } from "@/views/SkillsView";
import { McpsView } from "@/views/McpsView";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** The catalog floats over the user's current work; nested setup stays inside. */
export type PluginArea = "plugins" | "mcps" | "skills";

export function PluginsDialog({ onClose, area = "plugins", onAreaChange }: {
  onClose: () => void;
  area?: PluginArea;
  onAreaChange?: (area: PluginArea) => void;
}) {
  const t = useT();
  const content = useRef<HTMLDivElement>(null);
  const opener = useRef(document.activeElement);
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/65 backdrop-blur-[2px]" />
        <Dialog.Content
          data-testid="plugin-catalog-dialog"
          ref={content}
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            const previous = opener.current;
            const target = previous instanceof HTMLElement && previous.isConnected && previous !== document.body
              ? previous
              : document.querySelector<HTMLElement>('[data-testid="nav-row-plugins"]')
                ?? document.querySelector<HTMLElement>("main");
            if (!target) return;
            // This route-driven dialog has no Radix Trigger to restore focus to.
            event.preventDefault();
            const needsTabIndex = target.tagName === "MAIN" && !target.hasAttribute("tabindex");
            if (needsTabIndex) target.setAttribute("tabindex", "-1");
            target.focus({ preventScroll: true });
            if (needsTabIndex) target.removeAttribute("tabindex");
          }}
          onInteractOutside={(event) => {
            if (content.current?.querySelector('[aria-modal="true"]')) event.preventDefault();
          }}
          onEscapeKeyDown={(event) => {
            // The existing credential dialogs own Escape while they are open.
            if (content.current?.querySelector('[aria-modal="true"]')) event.preventDefault();
          }}
          className="fixed left-1/2 top-1/2 z-40 flex h-[min(82dvh,780px)] w-[min(800px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border border-border bg-popover text-foreground shadow-float outline-none [&_[aria-modal=true]]:overflow-y-auto"
        >
          <Dialog.Title className="sr-only">{t("plugins_view.title")}</Dialog.Title>
          <Dialog.Close asChild>
            <button type="button" aria-label={t("common.close")} className="absolute right-3 top-3 z-10 grid h-8 w-8 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <X className="h-4 w-4" aria-hidden />
            </button>
          </Dialog.Close>
          <Tabs defaultValue={area} value={onAreaChange ? area : undefined} onValueChange={(value) => onAreaChange?.(value as PluginArea)} className="flex min-h-0 flex-1 flex-col">
            <TabsList aria-label={t("nav.extensions")} className="mx-6 mb-2 mt-4 w-fit shrink-0 self-start">
              <TabsTrigger value="plugins">{t("nav.plugins")}</TabsTrigger>
              <TabsTrigger value="mcps">{t("nav.mcps")}</TabsTrigger>
              <TabsTrigger value="skills">{t("nav.skills")}</TabsTrigger>
            </TabsList>
            <TabsContent value="skills" className="mt-0 min-h-0 flex-1 overflow-hidden"><SkillsView /></TabsContent>
            <TabsContent value="plugins" className="mt-0 min-h-0 flex-1 overflow-hidden"><PluginsView inDialog /></TabsContent>
            <TabsContent value="mcps" className="mt-0 min-h-0 flex-1 overflow-hidden"><McpsView /></TabsContent>
          </Tabs>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
