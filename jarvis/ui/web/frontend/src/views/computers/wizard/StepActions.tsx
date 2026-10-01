/** The action row at the bottom of a wizard step: Back on the left, the step's buttons right. */
import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

export function StepActions({ onBack, children }: { onBack?: () => void; children?: ReactNode }) {
  const t = useT();
  return (
    <div className="mt-6 flex items-center justify-between gap-3 border-t border-border pt-4">
      <div>
        {onBack && (
          <Button type="button" variant="ghost" onClick={onBack} data-testid="wz-back">
            <ArrowLeft />
            {t("computers.back")}
          </Button>
        )}
      </div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}
