import { cva, type VariantProps } from "class-variance-authority";
import { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "./lib/utils";

const buttonStyles = cva(
  "inline-flex items-center justify-center rounded-xl text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400",
  {
    variants: {
      variant: {
        default: "bg-emerald-500 text-zinc-950 hover:bg-emerald-400",
        danger: "bg-red-500 text-white hover:bg-red-400",
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
  return <section className={cn("rounded-2xl border border-white/10 bg-panel p-4", className)}>{children}</section>;
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
  return <span className={cn("rounded-full px-2.5 py-1 text-xs font-semibold tracking-wide", tones[tone])}>{children}</span>;
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
  "h-11 w-full rounded-xl border border-white/10 bg-black/30 px-3 text-sm outline-none focus:ring-2 focus:ring-emerald-400";
