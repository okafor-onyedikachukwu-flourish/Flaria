/** Small shared building blocks for the admin console. */
import { AlertTriangle, CheckCircle2, X } from 'lucide-react';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from 'react';

type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'info';

const TONE: Record<Tone, string> = {
  neutral: 'border-rule bg-paper-deep text-ink-soft',
  good: 'border-stable/30 bg-stable-wash text-stable',
  warn: 'border-watch/40 bg-watch-wash text-watch-ink',
  bad: 'border-elevated/30 bg-elevated-wash text-elevated',
  info: 'border-scrub/30 bg-scrub-wash text-scrub-deep',
};

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`inline-flex max-w-full items-center gap-1 rounded-full [overflow-wrap:anywhere] border px-2 py-0.5 text-step--1 font-medium ${TONE[tone]}`}>{children}</span>;
}

export function SectionHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-prose">
        <h1 className="text-step-3 font-semibold">{title}</h1>
        <p className="mt-1 text-step--1 text-ink-soft">{description}</p>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function LoadState({ loading, error, empty, emptyText, children }: {
  loading: boolean; error: string | null; empty?: boolean; emptyText?: string; children: ReactNode;
}) {
  if (error) return <p role="alert" className="rounded-md border border-elevated/30 bg-elevated-wash px-4 py-3 text-step--1 text-elevated">{error}</p>;
  if (loading) return <p className="py-8 text-center text-step--1 text-ink-soft" role="status">Loading…</p>;
  if (empty) return <p className="py-8 text-center text-step--1 text-ink-soft">{emptyText}</p>;
  return <>{children}</>;
}

// ----------------------------------------------------------------------------- confirmation dialog
interface ConfirmRequest { title: string; body: string; confirm: string; danger?: boolean }
type ConfirmFn = (req: ConfirmRequest) => Promise<boolean>;
const ConfirmContext = createContext<ConfirmFn>(async () => false);
export const useConfirm = () => useContext(ConfirmContext);

// ----------------------------------------------------------------------------- toasts
interface ToastItem { id: number; text: string; tone: 'good' | 'bad' }
type ToastFn = (text: string, tone?: 'good' | 'bad') => void;
const ToastContext = createContext<ToastFn>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function AdminUiProvider({ children }: { children: ReactNode }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const confirm = useCallback<ConfirmFn>((req) => {
    setRequest(req);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  useEffect(() => {
    if (request && dialogRef.current && !dialogRef.current.open) dialogRef.current.showModal();
  }, [request]);

  const close = (ok: boolean) => {
    dialogRef.current?.close();
    resolver.current?.(ok);
    resolver.current = null;
    setRequest(null);
  };

  const toast = useCallback<ToastFn>((text, tone = 'good') => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      <ToastContext.Provider value={toast}>
        {children}
        <dialog
          ref={dialogRef}
          onCancel={(e) => {
            e.preventDefault();
            close(false);
          }}
          className="w-[min(28rem,calc(100vw-2rem))] rounded-instrument border border-rule bg-white p-0 text-ink shadow-xl backdrop:bg-ink/40"
          aria-labelledby="confirm-title"
        >
          {request && (
            <div className="p-6">
              <h2 id="confirm-title" className="text-step-2 font-semibold">{request.title}</h2>
              <p className="mt-2 text-step--1 text-ink-soft">{request.body}</p>
              <div className="mt-6 flex justify-end gap-2">
                <button type="button" className="btn-quiet py-2 text-step--1" onClick={() => close(false)}>Cancel</button>
                <button type="button" className={`${request.danger ? 'btn-danger' : 'btn-primary'} py-2 text-step--1`} onClick={() => close(true)} autoFocus>
                  {request.confirm}
                </button>
              </div>
            </div>
          )}
        </dialog>
        <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
          {toasts.map((t) => (
            <div key={t.id} className={`pointer-events-auto flex items-start gap-2 rounded-md border bg-white px-4 py-3 text-step--1 shadow-lg ${t.tone === 'good' ? 'border-stable/40' : 'border-elevated/40'}`}>
              {t.tone === 'good' ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-stable" aria-hidden /> : <AlertTriangle size={16} className="mt-0.5 shrink-0 text-elevated" aria-hidden />}
              <span className="flex-1">{t.text}</span>
              <button type="button" aria-label="Dismiss" className="text-ink-faint hover:text-ink" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>
                <X size={14} aria-hidden />
              </button>
            </div>
          ))}
        </div>
      </ToastContext.Provider>
    </ConfirmContext.Provider>
  );
}
