import { FlaskConical, Trash2, UserMinus } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { research, type MyParticipation } from '@/lib/research';
import { errorMessage } from '@/services/api';

const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'long' });

/** Account page: the participant's study status, withdrawal and data deletion. */
export function ResearchParticipation() {
  const [me, setMe] = useState<MyParticipation | null>(null);
  const [confirm, setConfirm] = useState<'withdraw' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: 'good' | 'bad'; text: string } | null>(null);

  const load = useCallback(() => {
    research.me().then(setMe).catch((err) => setMessage({ tone: 'bad', text: errorMessage(err) }));
  }, []);
  useEffect(load, [load]);

  const run = async (kind: 'withdraw' | 'delete') => {
    setBusy(true);
    setMessage(null);
    try {
      if (kind === 'withdraw') await research.withdraw();
      else await research.deleteMyData();
      setMessage({
        tone: 'good',
        text: kind === 'withdraw'
          ? 'You’ve withdrawn. Your data will be left out of every future research export.'
          : 'Your recorded measurements were deleted, and you’ve left the study.',
      });
      setConfirm(null);
      load();
    } catch (err) {
      setMessage({ tone: 'bad', text: errorMessage(err, 'That didn’t work. Try again.') });
    } finally {
      setBusy(false);
    }
  };

  if (me && !me.eligible) return null;

  return (
    <section className="panel bg-white" aria-labelledby="research-heading">
      <div className="border-b border-rule px-5 py-3">
        <h2 id="research-heading" className="flex items-center gap-2 font-sans text-step-0 font-semibold">
          <FlaskConical size={18} className="text-scrub" aria-hidden /> Research participation
        </h2>
      </div>
      <div className="space-y-4 px-5 py-5 text-step--1">
        {!me ? (
          <p className="text-ink-soft">Loading…</p>
        ) : me.consented ? (
          <p>
            You’re taking part{me.consented_at ? ` since ${fmt.format(new Date(me.consented_at))}` : ''}. Your study code is{' '}
            <span className="num font-semibold">{me.study_code}</span>. Researchers see only this code, never your name or email.
          </p>
        ) : (
          <p className="text-ink-soft">
            You’re not taking part right now.{' '}
            <Link to="/app" className="font-medium text-scrub-deep underline underline-offset-2">Join from the live session page</Link>{' '}
            whenever you like.
          </p>
        )}

        {message && <p role="status" className={message.tone === 'good' ? 'text-stable' : 'text-elevated'}>{message.text}</p>}

        {confirm ? (
          <div className="rounded-md border border-rule bg-paper-card p-4" role="group" aria-label="Confirm">
            <p className="font-medium">
              {confirm === 'withdraw'
                ? 'Withdraw from the study?'
                : 'Delete all your recorded measurements?'}
            </p>
            <p className="mt-1 text-ink-soft">
              {confirm === 'withdraw'
                ? 'Your data stays in your account but is left out of every future export. Anything exported before now can’t be recalled.'
                : 'Every session, result and baseline is permanently erased, and you leave the study. Your account stays, so you can sign in and start again.'}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className="btn-quiet py-2 text-step--1" onClick={() => setConfirm(null)} disabled={busy}>Cancel</button>
              <button type="button" className="btn-danger py-2 text-step--1" onClick={() => void run(confirm)} disabled={busy}>
                {confirm === 'withdraw' ? 'Withdraw' : 'Delete everything'}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {me?.consented && (
              <button type="button" className="btn-quiet py-2 text-step--1" onClick={() => setConfirm('withdraw')}>
                <UserMinus size={16} aria-hidden /> Withdraw from the study
              </button>
            )}
            <button type="button" className="btn-quiet py-2 text-step--1 text-elevated" onClick={() => setConfirm('delete')}>
              <Trash2 size={16} aria-hidden /> Delete my recorded measurements
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
