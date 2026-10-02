import { useUserName } from "@/hooks/useUserName";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { GigiMark } from "@/components/GigiMark";

/**
 * "Good morning, Ruben" — the front page's opening line, on both stages.
 *
 * The name comes from the Profile (useUserName); without one the greeting
 * simply has no name, it never invents one. The time of day follows the
 * machine's clock. `muted` shrinks it once a conversation is on screen so
 * the words being said, not the greeting, carry the page.
 */
export function Greeting({
  subtitle,
  muted = false,
}: {
  subtitle?: string;
  muted?: boolean;
}) {
  const t = useT();
  const name = useUserName();
  const text = fill(t(greetingKey(new Date().getHours())), {
    name_part: name ? `, ${name}` : "",
  });

  return (
    <div
      className={cn(
        "flex flex-col items-center text-center transition-[opacity,transform]",
        muted && "scale-[0.88] opacity-70",
      )}
      data-testid="home-greeting"
    >
      {/* The mark above, the line under it in regular weight — the Codex
          app's opening, quieter than a bold headline beside a logo. */}
      <GigiMark size={40} className="mb-4 rounded-xl opacity-90" />
      <h1 className="text-2xl font-normal tracking-tight text-foreground [text-wrap:balance]">
        {text}
      </h1>
      {subtitle && !muted && (
        <p className="mt-2 max-w-md text-base text-muted-foreground">{subtitle}</p>
      )}
    </div>
  );
}

/** Morning until 12, afternoon until 18, evening after. */
export function greetingKey(hour: number): string {
  if (hour < 12) return "home.greeting_morning";
  if (hour < 18) return "home.greeting_afternoon";
  return "home.greeting_evening";
}
