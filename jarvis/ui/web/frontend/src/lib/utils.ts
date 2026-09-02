import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/*
 * tailwind-merge only knows Tailwind's stock size names. Anything else that
 * starts with `text-` it files as a text COLOUR, so `cn("text-body",
 * "text-muted-foreground")` used to return just the colour and the element
 * fell back to the inherited size. Teaching it the design system's aliases
 * (display … micro) lets every component compose with `cn` again instead of
 * the `clsx` workaround several of them carry.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        { text: ["display", "page", "title", "reading", "body", "meta", "micro"] },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
