import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        // The navigation column's own ground (one step below the page).
        sidebar: "hsl(var(--sidebar))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        /*
         * Two colours whose ROLE is fixed while their value flips with the
         * theme. They exist because a large part of this UI is built from
         * translucent washes rather than solid fills — `bg-white/[0.03]` for a
         * raised surface, `border-white/[0.08]` for a hairline, `bg-black/60`
         * for a dialog backdrop — and every one of those is a literal colour
         * that only works on one ground.
         *
         *   sheen  — "lift this off the surface". White on dark, ink on light.
         *   scrim  — "push this behind something". Black on dark, warm charcoal
         *            on light, so a backdrop never turns blue-grey.
         *
         * Always use them WITH an alpha (`bg-sheen/[0.04]`, `bg-scrim/60`);
         * at full opacity they are just the extreme ends of the palette and
         * you almost certainly want --card / --background instead.
         */
        sheen: "rgb(var(--sheen-rgb) / <alpha-value>)",
        scrim: "rgb(var(--scrim-rgb) / <alpha-value>)",
      },
      /*
       * The radius scale, stated outright rather than derived from --radius.
       *
       * Derivation was the problem it looks like the solution to: with
       * `--radius` at 0.75rem, `md` (10px), `lg` (12px) and Tailwind's own
       * `xl` (12px) all landed within two pixels, so ten class names produced
       * about four distinguishable shapes and nothing read as contained by
       * anything else. The two hand-added steps (`control`, `surface`) were a
       * patch over that, and `surface` had drifted to exactly `md`.
       *
       * These are the Design.md's six values. Aliases are pointed AT them
       * rather than deleted -- `xl` resolves to the same 12px as `lg`, `3xl`
       * to the same 16px as `2xl` -- so the scale collapses without touching
       * ~1,350 class usages, and a stray `rounded-3xl` can no longer invent a
       * seventh shape.
       *
       *   4px   inline tags, chips          (sm)
       *   6px   compact rows, dense controls (control)
       *   8px   buttons, inputs             (md, surface)
       *   12px  cards, panes                (lg, xl)
       *   16px  large feature cards, rare   (2xl, 3xl)
       *   pill  badges, avatars             (full)
       */
      borderRadius: {
        none: "0px",
        sm: "4px",
        control: "6px",
        DEFAULT: "6px",
        md: "8px",
        surface: "8px",
        lg: "12px",
        xl: "12px",
        "2xl": "16px",
        "3xl": "16px",
        full: "9999px",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
      },
    },
  },
  plugins: [animate, typography],
};

export default config;
