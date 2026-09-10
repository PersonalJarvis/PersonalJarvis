import * as Dialog from "@radix-ui/react-dialog";
import { useRef } from "react";
import { X } from "lucide-react";
import { useT } from "@/i18n";
import { PluginsView } from "@/views/PluginsView";

/** The catalog floats over the user's current work; nested setup stays inside. */
export function PluginsDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const content = useRef<HTMLDivElement>(null);
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/65 backdrop-blur-[2px]" />
        <Dialog.Content
          data-testid="plugin-catalog-dialog"
          ref={content}
          aria-describedby={undefined}
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
          <PluginsView inDialog />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
