import { useMemo } from "react";

import {
  Combobox,
  type ComboboxGroup,
  type ComboboxOption,
} from "@/components/ui/combobox";

export type BrandedSelectOption = ComboboxOption;

export interface BrandedSelectProps {
  value: string;
  options: readonly BrandedSelectOption[];
  onValueChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  id?: string;
  ariaDescribedBy?: string;
  testId?: string;
  searchPlaceholder?: string;
  emptyLabel?: string;
}

/**
 * The app-wide replacement for a native `<select>`.
 *
 * Browser and operating-system option popups cannot be styled reliably, so a
 * native select always risks inserting foreign grey chrome into the warm dark
 * interface. This adapter keeps short, non-searchable choices on the same
 * accessible, portalled listbox foundation as the larger searchable pickers.
 */
export function BrandedSelect({
  value,
  options,
  onValueChange,
  ariaLabel,
  placeholder,
  disabled = false,
  className,
  id,
  ariaDescribedBy,
  testId,
  searchPlaceholder,
  emptyLabel,
}: BrandedSelectProps) {
  const groups = useMemo<ComboboxGroup[]>(
    () => [{ id: "options", options: [...options] }],
    [options],
  );

  return (
    <Combobox
      value={value}
      groups={groups}
      onChange={onValueChange}
      ariaLabel={ariaLabel}
      fallbackLabel={placeholder ?? value}
      searchPlaceholder={searchPlaceholder}
      emptyLabel={emptyLabel}
      disabled={disabled}
      className={className}
      id={id}
      ariaDescribedBy={ariaDescribedBy}
      testId={testId}
    />
  );
}
