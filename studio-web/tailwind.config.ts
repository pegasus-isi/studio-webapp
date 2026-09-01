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
        // Pegasus brand scale — cornflower/periwinkle (#314), sampled from the
        // redesign prototype. The scale name is unchanged so the ~250 existing
        // `pegasus-*` utilities reskin without touching every component.
        //
        // The 500→600 step is deliberately a cliff rather than an even
        // interval: 500 is the sampled hero value and 600 is the first shade
        // that carries white text at AA. Everything filled lives at 600+,
        // everything decorative at 500-.
        pegasus: {
          50: "#f3f6ff",
          100: "#e7eefc",
          200: "#cfdef7",
          300: "#aac4ee", // dark-mode text — 10.3:1 on the navy base
          400: "#84a9e4", // dark-mode accent
          500: "#6c9cde", // the sampled value; resting fills, non-critical borders
          600: "#3f6fb8", // filled actions — white text at 5.03:1
          700: "#35639f", // link/active text on light, and 600's hover
          800: "#274a78",
          900: "#1b3252",
        },
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
