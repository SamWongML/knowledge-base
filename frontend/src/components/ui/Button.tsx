import { type ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "link";
type Size = "sm" | "md" | "icon";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

const variants: Record<Variant, string> = {
  primary:
    "bg-foreground text-background hover:bg-foreground/85 shadow-[inset_0_-1px_0_oklch(0_0_0/0.2)]",
  secondary:
    "border border-border-strong/70 bg-surface-raised/60 hover:bg-surface hover:border-border-strong",
  ghost: "text-muted-foreground hover:text-foreground hover:bg-surface",
  danger:
    "border border-destructive/40 text-destructive hover:bg-destructive hover:text-background",
  link: "text-accent underline-offset-4 hover:underline px-0 h-auto",
};

const sizes: Record<Size, string> = {
  sm: "h-7 px-2.5 text-[12.5px] gap-1.5",
  md: "h-9 px-3.5 text-[13.5px] gap-2",
  icon: "size-8 justify-center",
};

export const buttonClass = (
  variant: Variant = "secondary",
  size: Size = "md",
  className?: string,
) =>
  cn(
    "inline-flex shrink-0 items-center rounded-md font-medium whitespace-nowrap transition-[background-color,color,border-color,opacity] duration-150 disabled:opacity-45 disabled:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
    variants[variant],
    sizes[size],
    className,
  );

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", className, type = "button", ...props },
  ref,
) {
  return (
    <button ref={ref} type={type} className={buttonClass(variant, size, className)} {...props} />
  );
});
