import { FlaskConical, LoaderCircle, ShieldAlert } from 'lucide-react';
import { useEffect, useId, useState } from 'react';

import { research, type Study } from '@/lib/research';
import { errorMessage } from '@/services/api';

/**
 * Participant information sheet and informed consent. Shown instead of the
 * session controls until the participant has agreed to the current version.
 */
export function ResearchConsent({ reconsent, onConsented }: { reconsent: boolean; onConsented: () => void }) {
  const [study, setStudy] = useState<Study | null>(null);
  const [checked, setChecked] = useState<boolean[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  const load = () =>
    research
      .study()
      .then((s) => {
        setStudy(s);
        setChecked(s.agreements.map(() => false));
      })
      .catch((err) => setError(errorMessage(err, 'The study information couldn’t load.')));
  useEffect(() => void load(), []);

  const agree = async () => {
    if (!study) return;
    setSaving(true);
    setError(null);
    try {
      await research.consent(study.version);
      onConsented();
    } catch (err) {
      setError(errorMessage(err, 'Your consent couldn’t be saved. Try again.'));
      void load(); // the wording may have changed: show the current version
    } finally {
      setSaving(false);
    }
  };

  const all = checked.length > 0 && checked.every(Boolean);

  return (
    <section className="panel bg-white p-5 sm:p-6" aria-labelledby={`${id}-title`}>
      <div className="flex items-start gap-3">
        <FlaskConical size={22} className="mt-1 shrink-0 text-scrub" aria-hidden />
        <div>
          <h2 id={`${id}-title`} className="text-step-2 font-semibold">
            {reconsent ? 'The study information has changed' : 'Before you start: join the study'}
          </h2>
          <p className="mt-1 text-step--1 text-ink-soft">
            {reconsent
              ? 'Please read it again. Sessions resume once you agree to the updated version.'
              : 'Flaria is running as a research study. Please read this, then decide whether to take part.'}
          </p>
        </div>
      </div>

      {!study && !error && <p className="mt-4 text-step--1 text-ink-soft" role="status">Loading the study information…</p>}
      {study && (
        <>
          <p className="mt-5 font-display text-step-1 font-semibold">{study.title}</p>
          {!study.approved && (
            <p className="mt-3 flex items-start gap-2 rounded-md border border-watch/40 bg-watch-wash px-3 py-2 text-step--1 text-watch-ink">
              <ShieldAlert size={16} className="mt-0.5 shrink-0" aria-hidden />
              This study hasn’t received ethics approval yet. You can try Flaria, but your data won’t be used in research until it does.
            </p>
          )}
          <div className="mt-4 max-h-[26rem] space-y-4 overflow-y-auto rounded-md border border-rule bg-paper-card p-4">
            {study.sections.map((s) => (
              <div key={s.heading}>
                <h3 className="font-sans text-step-0 font-semibold">{s.heading}</h3>
                <p className="mt-1 text-step--1 leading-relaxed text-ink-soft">{s.body}</p>
              </div>
            ))}
          </div>
          <fieldset className="mt-5 space-y-3">
            <legend className="sr-only">Consent statements</legend>
            {study.agreements.map((text, i) => (
              <label key={text} className="flex cursor-pointer items-start gap-3 text-step--1">
                <input
                  type="checkbox"
                  className="mt-0.5 h-5 w-5 shrink-0 accent-scrub"
                  checked={checked[i] ?? false}
                  onChange={(e) => setChecked((c) => c.map((v, j) => (j === i ? e.target.checked : v)))}
                />
                <span>{text}</span>
              </label>
            ))}
          </fieldset>
          {error && <p role="alert" className="mt-4 text-step--1 text-elevated">{error}</p>}
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button type="button" className="btn-primary" disabled={!all || saving} onClick={() => void agree()}>
              {saving && <LoaderCircle size={18} className="animate-spin" aria-hidden />}
              I agree and want to take part
            </button>
            <p className="text-step--1 text-ink-faint">Version {study.version}. You can withdraw any time in Account settings.</p>
          </div>
        </>
      )}
      {!study && error && <p role="alert" className="mt-4 text-step--1 text-elevated">{error}</p>}
    </section>
  );
}
