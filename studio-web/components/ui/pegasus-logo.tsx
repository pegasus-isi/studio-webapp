type PegasusLogoProps = {
  size?: number;
  className?: string;
  title?: string;
};

/**
 * Official PegasusAI winged-horse emblem, isolated from the brand wordmark for
 * use at chatbot-avatar sizes. Theme-specific art preserves contrast.
 */
export function PegasusLogo({
  size = 20,
  className,
  title,
}: PegasusLogoProps) {
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center ${className ?? ""}`}
      style={{ width: size, height: size }}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <img
        src="pegasus-chat-mark-light.png"
        alt=""
        className="block h-full w-full object-contain dark:hidden"
      />
      <img
        src="pegasus-chat-mark-dark.png"
        alt=""
        className="hidden h-full w-full object-contain dark:block"
      />
    </span>
  );
}
