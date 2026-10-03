export type OfficeFallbackReason = "graphics" | "reduced-motion" | null;

export function officeFallbackReason({
  webgl,
  reduced,
  coding,
  hasLedger,
}: {
  webgl: boolean;
  reduced: boolean;
  coding: boolean;
  hasLedger: boolean;
}): OfficeFallbackReason {
  if (!webgl) return "graphics";
  if (reduced && !coding && hasLedger) return "reduced-motion";
  return null;
}

export interface OfficeFallbackProps {
  message: string;
  actionLabel: string;
  onAction: () => void;
}

export function OfficeFallback({ message, actionLabel, onAction }: OfficeFallbackProps) {
  return (
    <div className="office-fallback">
      <p role="status">{message}</p>
      <button type="button" className="office-button" onClick={onAction}>
        {actionLabel}
      </button>
    </div>
  );
}
