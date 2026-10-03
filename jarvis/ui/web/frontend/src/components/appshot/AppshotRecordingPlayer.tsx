import * as Dialog from "@radix-ui/react-dialog";
import { Download, X } from "lucide-react";

import { useT } from "@/i18n";
import { appshotLibraryFileName, appshotLibraryImageUrl, type AppshotLibraryItem } from "@/lib/appshotApi";

export function AppshotRecordingPlayer({ item, onClose }: {
  item: AppshotLibraryItem | null;
  onClose: () => void;
}) {
  const t = useT();
  return (
    <Dialog.Root open={item !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      {item && (
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-[90] bg-scrim/60" />
          <Dialog.Content
            className="fixed left-1/2 top-1/2 z-[91] flex max-h-[90vh] w-[min(90vw,64rem)] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-xl border border-border bg-card p-5 text-foreground shadow-2xl"
            aria-describedby={undefined}
          >
            <Dialog.Title className="pr-9 text-base font-medium">
              {t("appshots.recording_title")}
            </Dialog.Title>
            <Dialog.Close className="absolute right-4 top-4 rounded-md p-1 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-border-strong"
              aria-label={t("appshot_editor.close")}>
              <X className="h-5 w-5" aria-hidden />
            </Dialog.Close>
            <video key={item.id} src={appshotLibraryImageUrl(item)} controls preload="metadata"
              poster={appshotLibraryImageUrl(item, true)}
              className="min-h-0 w-full flex-1 rounded-lg bg-secondary object-contain"
              aria-label={t("appshots.recording_title")} data-testid="appshot-library-player" />
            <a href={appshotLibraryImageUrl(item)} download={appshotLibraryFileName(item)}
              className="inline-flex items-center gap-2 self-start text-sm underline underline-offset-4">
              <Download className="h-4 w-4" aria-hidden />{t("appshots.recording_download")}
            </a>
          </Dialog.Content>
        </Dialog.Portal>
      )}
    </Dialog.Root>
  );
}
