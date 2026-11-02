import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Semantic tokens — backed by CSS vars that flip in .dark (globals.css)
        base: "var(--c-base)", // page background
        surface: "var(--c-surface)", // cards / panels
        muted: "var(--c-muted)", // subtle fills / hover
        line: "var(--c-line)", // borders
        fg: "var(--c-fg)", // primary text
        fgmuted: "var(--c-fgmuted)", // secondary text
        fgsubtle: "var(--c-fgsubtle)", // tertiary / placeholder
        accent: {
          DEFAULT: "var(--c-accent)",
          fg: "var(--c-accent-fg)",
        },
        // Pegasus brand scale: Tailwind's sky ramp, matching the JupyterHub
        // login page and Studio's JupyterLab theme (2026-11; it was
        // cornflower from #314). The scale name is unchanged so the
        // `pegasus-*` utilities reskin without touching every component.
        //
        // Two steps differ from Tailwind's sky, both for contrast. 600 is
        // the first shade that carries white text at AA (sky-600 #0284c7 is
        // 4.10:1). 50 and 100 are the selected/active tints, a step deeper
        // than sky-50/100, which were too faint to mark a choice on white.
        pegasus: {
          50: "#e0f2fe",
          100: "#cdeafd",
          200: "#bae6fd",
          300: "#7dd3fc", // dark-mode text
          400: "#38bdf8", // dark-mode accent
          500: "#0ea5e9", // JupyterLab's accent; rings, icons, non-critical borders
          600: "#0277b6", // filled actions, white text at 4.86:1
          700: "#0369a1", // link/active text on light, and 600's hover
          800: "#075985",
          900: "#0c4a6e",
        },
      },
      // One named size below text-xs, for badges, pills, keyboard hints and
      // small uppercase group labels. There were ten- and eleven-pixel
      // arbitrary values side by side for the same jobs (#554). Body and
      // helper text never go below text-xs.
      //
      // xs is a pixel up from Tailwind's 12px: it carries most helper text
      // and hints (it outnumbers text-sm), and at 12px in Outfit it read as
      // too small on a laptop screen and in the paper's screenshots.
      fontSize: {
        "2xs": ["12px", { lineHeight: "16px" }],
        xs: ["13px", { lineHeight: "18px" }],
      },
      fontFamily: {
        sans: ["var(--font-outfit)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};

export default config;
