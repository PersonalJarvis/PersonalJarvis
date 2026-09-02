import type { ReactNode } from "react";

import { PageHeader } from "@/components/layout/PageHeader";

/**
 * Compatibility face of `PageHeader`: the header the views adopted under this
 * name keeps its props (`subtitle`) and lands on the one design. New code
 * imports `PageHeader` directly.
 */
export function SectionHeader({
  icon,
  title,
  subtitle,
  actions,
  className,
}: {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <PageHeader
      icon={icon}
      title={title}
      description={subtitle}
      actions={actions}
      className={className}
    />
  );
}
