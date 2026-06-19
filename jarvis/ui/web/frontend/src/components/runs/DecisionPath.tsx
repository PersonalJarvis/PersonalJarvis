import type { DecisionStep } from "./types";

const KIND_ICON: Record<string, string> = {
  tier: "◆", route: "→", risk: "⚖", brain: "🧠", mission: "⚙", fallback: "↺",
};

export function DecisionPath({ steps }: { steps: DecisionStep[] }) {
  if (steps.length === 0) return <span className="text-muted-foreground/60">n/a</span>;
  return (
    <ol className="space-y-0.5 text-[11px]">
      {steps.map((s, i) => (
        <li key={i} className="flex gap-2">
          <span className="w-4 shrink-0 text-center text-muted-foreground">{KIND_ICON[s.kind] ?? "·"}</span>
          <span>{s.label}</span>
          {s.detail && <span className="text-muted-foreground">— {s.detail}</span>}
        </li>
      ))}
    </ol>
  );
}
