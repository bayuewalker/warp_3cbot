import { type Lang, t } from "./i18n";
import { cva, type VariantProps } from "class-variance-authority";
import { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "./lib/utils";

const buttonStyles = cva(
  "inline-flex items-center justify-center rounded-lg text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400",
  {
    variants: {
      variant: {
        default: "bg-blue-700 text-white hover:bg-blue-600",
        danger: "border border-red-400/50 bg-red-500/10 text-red-200 hover:bg-red-500/20",
        outline: "border border-white/15 bg-white/5 hover:bg-white/10",
        ghost: "hover:bg-white/10",
      },
      size: {
        default: "h-11 px-4",
        sm: "h-9 px-3",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export function Button({
  className,
  variant,
  size,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonStyles>) {
  return <button className={cn(buttonStyles({ variant, size }), className)} {...props} />;
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("surface", className)}>{children}</section>;
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const tones = {
    neutral: "bg-white/10 text-zinc-200",
    good: "bg-emerald-500/15 text-emerald-300",
    warn: "bg-amber-500/15 text-amber-200",
    bad: "bg-red-500/15 text-red-200",
    info: "bg-sky-500/15 text-sky-200",
  };
  return <span className={cn("badge", tones[tone])}>{children}</span>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5 text-sm">
      <span className="text-zinc-300">{label}</span>
      {children}
      {hint ? <span className="block text-xs text-zinc-500">{hint}</span> : null}
    </label>
  );
}

export const inputClass =
  "h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3 text-sm outline-none focus:ring-2 focus:ring-blue-400";

export function Loading({ lang }: { lang: Lang }) {
  return (
    <div className="skeleton" role="status" aria-busy="true">
      <span className="sr-only">{t(lang, "loading")}</span>
      <i />
      <i />
      <i />
    </div>
  );
}
export function Problem({ lang, retry }: { lang: Lang; retry: () => void }) {
  return (
    <Card className="problem">
      <p role="alert">{t(lang, "noSnapshot")}</p>
      <Button variant="outline" onClick={retry}>
        {t(lang, "retry")}
      </Button>
    </Card>
  );
}
