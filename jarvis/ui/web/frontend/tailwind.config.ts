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
        success: "hsl(var(--success))",
        /*
         * "Degraded" — the third and last status hue, beside --success (life)
         * and --destructive (fault). It exists because a partially-working
         * thing used to be painted with --foreground or a raw amber literal,
         * which reads either as "fine" or as decoration. Status is the only
         * place hue is allowed to appear, so it needs its own token.
         */
        warning: "hsl(var(--warning))",
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
        /*
         * The three ends of the scale that were previously reached for with an
         * alpha instead of a name.
         *
         *   border-strong      the rim of something that FLOATS or is focused
         *                      — a composer outline, a popover edge, a focus
         *                      ring. --border stays the structural hairline.
         *   foreground-strong  headings and headline numbers. The ink ceiling
         *                      sits BELOW pure white, so a title can be louder
         *                      than body text without --primary, which is a
         *                      fill and never an ink.
         *   faint-foreground   placeholder and disabled text ONLY. Everything
         *                      dimmer than meta used to be written as
         *                      `text-foreground/40`, which is an alpha doing
         *                      a hierarchy's job and lands differently on
         *                      every ground it happens to sit on.
         */
        "border-strong": "hsl(var(--border-strong))",
        "foreground-strong": "hsl(var(--foreground-strong))",
        "faint-foreground": "hsl(var(--faint-foreground))",
      },
      /*
       * The three families, restated here so the utility layer and the base
       * layer cannot disagree.
       *
       * index.css declares `body`, `.font-display` and `.font-mono` directly.
       * Tailwind generated no `font-sans` / `font-display` / `font-mono`
       * utility of its own, so `font-sans` fell through to Tailwind's default
       * system stack — a different typeface from the one the page was already
       * rendering in. These mirror index.css exactly; the base layer keeps its
       * feature-settings and tracking, which are different properties and so
       * survive the utility.
       */
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        display: [
          "Space Grotesk",
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SF Mono",
          "Menlo",
          "monospace",
        ],
      },
      /*
       * The seven-step type scale, each step carrying its own line height and
       * tracking so a size cannot be picked without the leading that goes with
       * it.
       *
       * No named scale existed, so authors reached for arbitrary values: of
       * ~2,956 explicit size classes, 2,181 are 12px or under, bottoming out at
       * a single `text-[7px]`. Named steps give the seven sizes the design
       * actually has, and 11px (`text-micro`) is the hard floor — there is no
       * step below it to reach for.
       *
       * Tailwind's own xs/sm/base/lg/... entries are deliberately left intact:
       * thousands of call sites use them and this wave does not migrate them.
       *
       *   display  24/1.2   section headline, the largest thing on a screen
       *   page     20/1.3   the title of a view
       *   title    15/1.4   card and row titles (600)
       *   reading  15/1.6   running prose — chat, docs, descriptions
       *   body     14/1.5   the default for everything else
       *   meta     13/1.45  timestamps, table heads, secondary detail
       *   micro    11/1.4   badges and dense labels. THE FLOOR.
       */
      fontSize: {
        display: [
          "24px",
          { lineHeight: "1.2", letterSpacing: "-0.02em", fontWeight: "600" },
        ],
        page: ["20px", { lineHeight: "1.3", letterSpacing: "-0.015em" }],
        title: ["15px", { lineHeight: "1.4" }],
        reading: ["15px", { lineHeight: "1.6" }],
        body: ["14px", { lineHeight: "1.5" }],
        meta: ["13px", { lineHeight: "1.45" }],
        micro: ["11px", { lineHeight: "1.4", letterSpacing: "0.01em" }],
      },
      /*
       * Two elevations, and nothing else.
       *
       * There was no boxShadow scale at all, so the ~60 components applying a
       * bare `shadow` got Tailwind's default `rgb(0 0 0 / 0.1)` — black at ten
       * percent on a #0A0A0A ground, which is mathematically invisible. The
       * separation those components were asking for has to come from the
       * theme's own channels instead:
       *
       *   rim    an inset top highlight. What a resting object gets — a card,
       *          a bubble, a tile. It lifts without casting anything.
       *   float  a real cast shadow plus a --border-strong rim. ONLY for
       *          things that genuinely hover over the page: popover, dialog,
       *          menu, tooltip.
       *
       * Both are built from --sheen-rgb / --scrim-rgb, so they invert with the
       * theme instead of assuming a dark ground. Tailwind's own steps are
       * re-pointed at these two rather than removed, so every existing bare
       * `shadow` becomes visible without being edited: the small steps resolve
       * to `rim`, the large ones to `float`.
       */
      boxShadow: {
        rim: "inset 0 1px 0 rgb(var(--sheen-rgb) / 0.05)",
        float:
          "0 12px 32px -8px rgb(var(--scrim-rgb) / 0.55), 0 0 0 1px hsl(var(--border-strong))",
        none: "none",
        sm: "inset 0 1px 0 rgb(var(--sheen-rgb) / 0.05)",
        DEFAULT: "inset 0 1px 0 rgb(var(--sheen-rgb) / 0.05)",
        inner: "inset 0 1px 0 rgb(var(--sheen-rgb) / 0.05)",
        md: "0 12px 32px -8px rgb(var(--scrim-rgb) / 0.55), 0 0 0 1px hsl(var(--border-strong))",
        lg: "0 12px 32px -8px rgb(var(--scrim-rgb) / 0.55), 0 0 0 1px hsl(var(--border-strong))",
        xl: "0 12px 32px -8px rgb(var(--scrim-rgb) / 0.55), 0 0 0 1px hsl(var(--border-strong))",
        "2xl":
          "0 12px 32px -8px rgb(var(--scrim-rgb) / 0.55), 0 0 0 1px hsl(var(--border-strong))",
      },
      /*
       * The four spacing steps, named for what they separate.
       *
       * Additive only — Tailwind's numeric scale is untouched. The names exist
       * because the rhythm is a decision, not an arithmetic: `gap-2` says
       * "eight pixels", `gap-row` says "these two things belong to the same
       * row". The step that is actually missing across the app is `group`:
       * sections currently run their groups together at 12–16px, which is why
       * a screen reads as one undifferentiated list.
       *
       *   row    8px   items inside one row
       *   stack  12px  rows inside one group
       *   block  20px  padding inside a card or panel
       *   group  32px  between two groups in a section
       */
      spacing: {
        row: "8px",
        stack: "12px",
        block: "20px",
        group: "32px",
      },
      /*
       * The three measures. Rule 1 — lift scales inversely with area — needs a
       * cap to point at: a surface only earns --card while it stays sized to
       * its content, and these are the widths that keep it there. `reading`
       * is also the line length prose stops growing at.
       */
      maxWidth: {
        reading: "720px",
        form: "640px",
        page: "1080px",
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
