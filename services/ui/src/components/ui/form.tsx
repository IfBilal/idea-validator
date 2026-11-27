import { useId, useState, type InputHTMLAttributes, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/lib/utils";

const control =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-1 aria-[invalid=true]:border-red-500";

interface FieldProps {
  label: string;
  hint?: string;
  error?: string;
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode;
}

/** Label + control + hint + error, wired with htmlFor / aria-describedby / aria-invalid. */
export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {hint && (
        <p id={hintId} className="text-xs text-[var(--muted)]">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-xs text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(control, className)} {...props} />;
}

export function Alert({ children, tone = "error" }: { children: ReactNode; tone?: "error" | "info" }) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        tone === "error"
          ? "border-red-500 text-red-700 dark:text-red-300"
          : "border-[var(--border)] text-[var(--muted)]",
      )}
    >
      {children}
    </div>
  );
}

interface TagInputProps {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  value: string[];
  onChange: (next: string[]) => void;
  max: number;
  placeholder?: string;
}

/** Type a tag and press Enter or comma; Backspace on an empty box removes the last one. */
export function TagInput({ id, describedBy, invalid, value, onChange, max, placeholder }: TagInputProps) {
  const [draft, setDraft] = useState("");

  function commit() {
    const tag = draft.trim().replace(/,+$/, "").trim();
    setDraft("");
    if (!tag || value.includes(tag) || value.length >= max) return;
    onChange([...value, tag]);
  }

  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  return (
    <div className="space-y-2">
      <ul className="flex flex-wrap gap-2" aria-label="Selected">
        {value.map((tag) => (
          <li key={tag} className="flex items-center gap-1 rounded-full border border-[var(--border)] px-2 py-0.5 text-xs">
            {tag}
            <button
              type="button"
              aria-label={`Remove ${tag}`}
              className="px-1"
              onClick={() => onChange(value.filter((t) => t !== tag))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <input
        id={id}
        aria-describedby={describedBy}
        aria-invalid={invalid}
        className={control}
        value={draft}
        maxLength={50}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={commit}
      />
    </div>
  );
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(control, className)} {...props}>
      {children}
    </select>
  );
}
