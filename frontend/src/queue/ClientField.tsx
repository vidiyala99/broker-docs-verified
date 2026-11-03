import { useId } from "react";

/** Work queue W4: the optional Client field of the start screens (labelled above, 120 characters, API.md `client_name`). */
export function ClientField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div className="client-field">
      <label htmlFor={id} className="client-field__label">
        Client <span className="client-field__optional">optional</span>
      </label>
      <input
        id={id}
        className="client-field__input"
        value={value}
        maxLength={120}
        autoComplete="organization"
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
