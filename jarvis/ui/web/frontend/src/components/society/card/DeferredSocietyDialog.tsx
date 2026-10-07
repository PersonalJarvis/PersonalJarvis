import { Component, Suspense, lazy, useEffect, useRef, useState, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, X } from "lucide-react";
import { useT } from "@/i18n";

type Props<P extends object> = {
  load: () => Promise<{ default: ComponentType<P> }>;
  dialogProps: P;
  title: string;
  onClose: () => void;
};

/** Mounted only on intent; a slow or failed import must remain dismissible. */
export function DeferredSocietyDialog<P extends object>({ load, dialogProps, title, onClose }: Props<P>) {
  // A new opening gets a fresh lazy wrapper, so a rejected load is retryable.
  // Updates within that opening keep the same component and its draft state.
  const [LoadedDialog] = useState(() => lazy(async () => {
    const { default: Content } = await load();
    // A named prop keeps React.lazy's ref-prop transformation away from the
    // generic dialog props while preserving the loader's exact props contract.
    return { default: ({ dialogProps: props }: { dialogProps: P }) => <Content {...props} /> };
  }));
  const [trigger] = useState(() => document.activeElement);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      // Skip StrictMode's rehearsal and a handoff to another dialog. The
      // fallback and loaded dialog otherwise restore the same original trigger.
      queueMicrotask(() => {
        if (!mounted.current && !document.querySelector('[role="dialog"]') && trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
      });
    };
  }, [trigger]);
  const fallback = (failed: boolean) => <PendingDialog title={title} onClose={onClose} failed={failed} />;
  return <DialogLoadBoundary fallback={fallback(true)}>
    <Suspense fallback={fallback(false)}>
      <LoadedDialog dialogProps={dialogProps} />
    </Suspense>
  </DialogLoadBoundary>;
}

function PendingDialog({ title, onClose, failed }: { title: string; onClose: () => void; failed: boolean }) {
  const t = useT();
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/60" />
      <Dialog.Content
        onCloseAutoFocus={(event) => event.preventDefault()}
        className="fixed left-1/2 top-1/2 z-[60] w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-card p-5 text-foreground shadow-float"
      >
        <Dialog.Title className="pr-8 text-base font-semibold">{title}</Dialog.Title>
        <Dialog.Description role={failed ? "alert" : "status"} aria-busy={!failed} className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
          {!failed && <Loader2 aria-hidden className="h-4 w-4 animate-spin motion-reduce:animate-none" />}
          {t(failed ? "view_error_boundary.title" : "common.loading")}
        </Dialog.Description>
        <Dialog.Close className="absolute right-3 top-3 rounded-md p-1.5 hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={t("society.card.close")}>
          <X aria-hidden className="h-4 w-4" />
        </Dialog.Close>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

class DialogLoadBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("Society dialog could not open", { error, componentStack: info.componentStack });
  }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}
