import type { ReactNode } from "react";

import { EmptyState as UiEmptyState } from "@/components/ui/empty-state";

/**
 * Compatibility face of `components/ui/empty-state`.
 *
 * The original layout-level component took `body` + `action`; the primitive
 * takes `description` + `actions` and always wants a title. Existing call
 * sites keep their props and land on the one design — new code imports the
 * primitive directly.
 */
export function EmptyState({
  icon,
  title,
  body,
  action,
  className,
}: {
  icon?: ReactNode;
  title?: string;
  body: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <UiEmptyState
      icon={icon}
      title={title ?? body}
      description={title ? body : undefined}
      actions={action}
      className={className}
    />
  );
}
