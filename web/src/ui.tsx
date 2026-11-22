import { type Lang, t } from "./i18n";
import { cva, type VariantProps } from "class-variance-authority";
import { ButtonHTMLAttributes, ReactNode } from "react";
import { Inbox, TriangleAlert, type LucideIcon } from "lucide-react";
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
    <div className="skeleton surface" role="status" aria-busy="true">
      <span className="sr-only">{t(lang, "loading")}</span>
      <i />
      <i />
      <i />
    </div>
  );
}
export function Problem({ lang, retry, detail }: { lang: Lang; retry: () => void; detail?: string | null }) {
  return (
    <Card className="problem">
      <TriangleAlert size={20} aria-hidden="true" />
      <div>
        <p role="alert">{t(lang, "noSnapshot")}</p>
        {detail ? <p className="card-help">{detail}</p> : null}
      </div>
      <Button variant="outline" onClick={retry}>
        {t(lang, "retry")}
      </Button>
    </Card>
  );
}

/** Empty state: icon plus one plain sentence. */
export function Empty({ text, icon: Icon = Inbox }: { text: string; icon?: LucideIcon }) {
  return (
    <Card className="empty">
      <span className="empty-icon" aria-hidden="true">
        <Icon size={20} />
      </span>
      <p>{text}</p>
    </Card>
  );
}

/** Card heading: display-font title, optional icon, one help line and a right-side slot. */
export function CardHead({
  title,
  icon: Icon,
  help,
  aside,
  as: Tag = "h2",
}: {
  title: ReactNode;
  icon?: LucideIcon;
  help?: ReactNode;
  aside?: ReactNode;
  as?: "h2" | "h3";
}) {
  return (
    <div className="card-head">
      <div className="card-head-text">
        <Tag className="panel-title">
          {Icon ? <Icon size={18} /> : null}
          {title}
        </Tag>
        {help ? <p className="card-help">{help}</p> : null}
      </div>
      {aside ? <div className="card-head-aside">{aside}</div> : null}
    </div>
  );
}

/** Selectable option card: used for toggles and preset pickers. */
export function Choice({
  pressed,
  title,
  detail,
  onClick,
  disabled,
  state,
}: {
  pressed: boolean;
  title: ReactNode;
  detail?: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  state?: ReactNode;
}) {
  return (
    <button type="button" className="choice" aria-pressed={pressed} disabled={disabled} onClick={onClick}>
      <span className="choice-top">
        <span className="choice-title">{title}</span>
        <span className="choice-state">{state ?? <span className="choice-dot" aria-hidden="true" />}</span>
      </span>
      {detail ? <span className="choice-detail">{detail}</span> : null}
    </button>
  );
}

/** Status line under a form or card. */
export function Note({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" }) {
  return (
    <p role="status" className={cn("form-note", `form-note-${tone}`)}>
      {children}
    </p>
  );
}

/** Label/value rows. */
export function KV({ rows }: { rows: [ReactNode, ReactNode, string?][] }) {
  return (
    <dl className="kv">
      {rows.map(([label, value, hint], index) => (
        <div key={index}>
          <dt>
            {label}
            {hint ? <small>{hint}</small> : null}
          </dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
