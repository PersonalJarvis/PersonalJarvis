import { MessageSquare, Mic } from "lucide-react";

import { useHomeStore } from "@/store/home";
import { useEventStore } from "@/store/events";
import type { HomeSurface } from "@/lib/homeSurface";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * `Voice | Chat` — the front page's one switch, at the top of the sidebar.
 *
 * Pressing either half also lands on the front page: from any other section
 * the switch is the fastest way back to talking, and a switch that only
 * changed a hidden preference would read as broken.
 */
export function SurfaceSwitch({ className }: { className?: string }) {
  const t = useT();
  const surface = useHomeStore((s) => s.surface);
  const setSurface = useHomeStore((s) => s.setSurface);
  const setActive = useEventStore((s) => s.setActiveSection);

  const pick = (next: HomeSurface) => {
    setSurface(next);
    setActive("chats");
  };

  return (
    <div
      role="tablist"
      aria-label={t("sidebar.surface_hint")}
      data-testid="home-surface-switch"
      className={cn(
        "grid grid-cols-2 gap-0.5 rounded-xl border border-border bg-secondary p-0.5",
        className,
      )}
    >
      <SurfaceTab
        active={surface === "voice"}
        onClick={() => pick("voice")}
        icon={<Mic aria-hidden className="h-3.5 w-3.5" />}
        label={t("sidebar.surface_voice")}
        testId="home-surface-voice"
      />
      <SurfaceTab
        active={surface === "chat"}
        onClick={() => pick("chat")}
        icon={<MessageSquare aria-hidden className="h-3.5 w-3.5" />}
        label={t("sidebar.surface_chat")}
        testId="home-surface-chat"
      />
    </div>
  );
}

function SurfaceTab({
  active,
  onClick,
  icon,
  label,
  testId,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      data-testid={testId}
      onClick={onClick}
      className={cn(
        "flex items-center justify-center gap-1.5 rounded-[10px] px-2 py-1.5 text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
        active
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      {label}
    </button>
  );
}
