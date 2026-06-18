/**
 * PegasusAI mark: a stylized wing of three layered, curved feathers rising to
 * an AI sparkle, on a teal-to-navy badge. Used as the chat logo / assistant
 * avatar and the studio brand mark. Tuned to read from ~18px up.
 */
export function PegasusLogo({ size = 20 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id="pegasus-badge" x1="2" y1="22" x2="22" y2="2">
          <stop offset="0%" stopColor="#0f2942" />
          <stop offset="55%" stopColor="#1e3a5f" />
          <stop offset="100%" stopColor="#0891b2" />
        </linearGradient>
      </defs>

      <circle cx="12" cy="12" r="12" fill="url(#pegasus-badge)" />

      {/* Wing — three overlapping curved feather blades fanning up-right,
          brightening toward the leading edge for depth. Each blade is a
          teardrop: convex leading edge, concave trailing edge. */}
      <path
        d="M6 16.6 C 8.7 11.2 12.6 8.6 18.2 7.4 C 14.6 10.2 11.6 13 9.1 16 C 8.1 16.2 7 16.5 6 16.6 Z"
        fill="#7fe3f5"
      />
      <path
        d="M6.7 17.6 C 9.4 12.8 12.9 10.5 17.6 9.7 C 14.4 12.2 11.7 14.6 9.6 17 C 8.7 17.2 7.6 17.5 6.7 17.6 Z"
        fill="#cdeffb"
      />
      <path
        d="M7.6 18.7 C 10 14.7 13 12.8 16.7 12.2 C 14 14.3 11.7 16.5 10.1 18.5 C 9.3 18.6 8.4 18.7 7.6 18.7 Z"
        fill="#ffffff"
      />

      {/* AI sparkle in the open upper-right */}
      <path
        d="M17.9 3.6 C18.15 5.35 18.75 5.95 20.5 6.2 C18.75 6.45 18.15 7.05 17.9 8.8 C17.65 7.05 17.05 6.45 15.3 6.2 C17.05 5.95 17.65 5.35 17.9 3.6 Z"
        fill="#ffffff"
      />
    </svg>
  );
}
