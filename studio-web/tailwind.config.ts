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
        // Pegasus brand scale, retuned to the official site's sky accent.
        pegasus: {
          50: "#f0f9ff",
          100: "#e0f2fe",
          200: "#bae6fd",
          300: "#7dd3fc",
          400: "#38bdf8", // dark-mode accent
          500: "#0ea5e9", // light-mode accent
          600: "#0284c7",
          700: "#0369a1",
          800: "#075985",
          900: "#0c4a6e",
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
