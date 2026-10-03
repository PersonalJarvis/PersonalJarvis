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
