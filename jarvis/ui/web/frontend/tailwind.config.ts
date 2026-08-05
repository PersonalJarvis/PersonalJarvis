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
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        /*
         * Two tighter steps for dense, tool-shaped surfaces — the Agentic IDE's
         * launcher above all (see components/agentic/controls.tsx).
         *
         * They exist because the three above do not separate: `--radius` is
         * 0.75rem, so `md`, `lg` and Tailwind's own `xl` all land between 10 and
         * 12 px. That is a fine radius for a settings card and no radius scale
         * at all for a screen that stacks a 32 px control inside a row inside a
         * panel — everything came out equally round, so nothing read as
         * contained by anything else. Additive: no existing class changes.
         */
        control: "6px",
        surface: "10px",
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
