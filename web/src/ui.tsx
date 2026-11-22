import { type Lang, t } from "./i18n";
import { cva, type VariantProps } from "class-variance-authority";
import { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "./lib/utils";

const buttonStyles = cva(
  "btn inline-flex items-center justify-center gap-2 text-sm font-semibold transition disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "btn-primary",
        danger: "btn-danger",
        outline: "btn-ghost",
        ghost: "btn-text",
      },
      size: {
        default: "h-11 px-4",
        sm: "h-11 px-3",
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
  return <span className={cn("badge", `badge-${tone}`)}>{children}</span>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5 text-sm">
      <span className="field-label">{label}</span>
      {children}
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export const inputClass =
  "field-input h-11 w-full px-3 outline-none";

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
