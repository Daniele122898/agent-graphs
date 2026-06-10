// Small, coherent UI primitives backed by index.css. Components use these
// instead of raw HTML controls so styling stays consistent everywhere.

import type { ButtonHTMLAttributes, SelectHTMLAttributes, InputHTMLAttributes, TextareaHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  size,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "lg" }) {
  const cls = ["btn", `btn-${variant}`, size ? `btn-${size}` : "", className].filter(Boolean).join(" ");
  return <button className={cls} {...rest} />;
}

export function IconButton({ className = "", ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`icon-btn ${className}`} {...rest} />;
}

export function Select({ className = "", ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={`select ${className}`} {...rest} />;
}

export function TextInput({ className = "", ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`input ${className}`} {...rest} />;
}

export function TextArea({ className = "", ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={`textarea ${className}`} {...rest} />;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: "block" }}>
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

type ChipTone = "default" | "primary" | "success" | "warning" | "danger";
export function Chip({ tone = "default", children }: { tone?: ChipTone; children: ReactNode }) {
  return <span className={tone === "default" ? "chip" : `chip chip-${tone}`}>{children}</span>;
}
