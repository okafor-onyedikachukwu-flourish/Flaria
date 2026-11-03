import { Eye, EyeOff } from 'lucide-react';
import { useId, useState } from 'react';

interface Props {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: 'current-password' | 'new-password';
  hint?: string;
  error?: string | null;
}

export function PasswordField({ label, value, onChange, autoComplete, hint, error }: Props) {
  const id = useId();
  const hintId = useId();
  const [visible, setVisible] = useState(false);
  const message = error ?? hint;
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-step--1 font-medium">{label}</label>
      <div className="relative">
        <input
          id={id}
          className="field pr-11"
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          maxLength={128}
          required
          value={value}
          aria-invalid={!!error}
          aria-describedby={message ? hintId : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-ink-soft hover:text-ink"
          aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
        >
          {visible ? <EyeOff size={18} aria-hidden /> : <Eye size={18} aria-hidden />}
        </button>
      </div>
      {message && <p id={hintId} className={`mt-1.5 text-step--1 ${error ? 'text-elevated' : 'text-ink-soft'}`}>{message}</p>}
    </div>
  );
}
