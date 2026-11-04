import { Search } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { errorMessage } from '@/services/api';

import { adminApi, type AuditEvent } from './adminApi';
import { absolute, ACTION_LABEL, roleLabel } from './format';
import { Badge, LoadState, SectionHeader } from './ui';

const CATEGORIES = [
  ['', 'All events'],
  ['auth', 'Sign-in and accounts'],
  ['session', 'Sessions'],
  ['report', 'Results'],
  ['care_note', 'Care notes'],
  ['research', 'Research'],
  ['admin', 'Admin actions'],
  ['cli', 'Command line'],
] as const;

function tone(action: string): 'bad' | 'warn' | 'info' | 'neutral' {
  if (action === 'auth.refresh_reuse' || action === 'admin.disable_user' || action === 'research.data_deleted') return 'bad';
  if (['auth.login_failed', 'auth.password_change_failed', 'auth.password_reset_throttled', 'report.red_flags'].includes(action)) return 'warn';
  if (action.startsWith('admin.') || action.startsWith('cli.')) return 'info';
  return 'neutral';
}

function describe(e: AuditEvent): string {
  const d = e.detail;
  if (e.action === 'admin.set_role' && typeof d.from === 'string' && typeof d.to === 'string') return `${roleLabel(d.from)} to ${roleLabel(d.to)}`;
  if (e.action === 'session.end') return `${String(d.status ?? '').toLowerCase()}, ${d.ticks ?? 0} updates`;
  if (e.action === 'report.red_flags' && Array.isArray(d.red_flags)) return `${d.red_flags.length} symptom${d.red_flags.length === 1 ? '' : 's'} reported`;
  if (e.action === 'admin.sign_out_user' && typeof d.refresh_tokens_revoked === 'number') return `${d.refresh_tokens_revoked} device session${d.refresh_tokens_revoked === 1 ? '' : 's'} ended`;
  return '';
}

export function AuditPanel() {
  const [category, setCategory] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AuditEvent[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setQuery(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback((before: number | null) => {
    return adminApi.audit({ category, search: query, before }).then((page) => {
      setItems((prev) => (before && prev ? [...prev, ...page.items] : page.items));
      setNext(page.next_before);
      setError(null);
    });
  }, [category, query]);

  useEffect(() => {
    setItems(null);
    load(null).catch((err) => setError(errorMessage(err, 'The audit log couldn’t load.')));
  }, [load]);

  const more = async () => {
    setLoadingMore(true);
    try {
      await load(next);
    } catch (err) {
      setError(errorMessage(err, 'Older events couldn’t load.'));
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <section aria-labelledby="audit-title">
      <SectionHeader title="Audit log" description="Append-only record of sign-ins, sessions, results, care notes and every admin action. Telemetry values are never written here." />
      <div className="panel bg-white">
        <div className="flex flex-wrap items-end gap-3 border-b border-rule p-4">
          <label>
            <span className="mb-1 block text-step--1 font-medium">Type</span>
            <select className="field py-2" value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="min-w-[16rem] flex-1">
            <span className="mb-1 block text-step--1 font-medium">Person</span>
            <span className="relative block">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" aria-hidden />
              <input className="field py-2 pl-9" placeholder="Name or email of who acted or was affected" value={search} onChange={(e) => setSearch(e.target.value)} />
            </span>
          </label>
        </div>
        <LoadState loading={!items && !error} error={error} empty={items?.length === 0} emptyText="No events match these filters.">
          {items && (
            <div className="relative overflow-x-auto">
              <table className="w-full min-w-[56rem] text-left text-step--1">
                <thead className="border-b border-rule bg-paper-card text-ink-soft">
                  <tr>
                    <th scope="col" className="px-4 py-2.5 font-medium">When</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Event</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">By</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Affected</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">IP address</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((e) => (
                    <tr key={e.id} className="border-b border-rule/70 last:border-0">
                      <td className="num whitespace-nowrap px-4 py-2.5 text-ink-soft">{absolute(e.ts)}</td>
                      <td className="px-4 py-2.5">
                        <Badge tone={tone(e.action)}>{ACTION_LABEL[e.action] ?? e.action}</Badge>
                        {describe(e) && <span className="ml-2 text-ink-soft">{describe(e)}</span>}
                      </td>
                      <td className="px-4 py-2.5">{e.actor_name ?? <span className="text-ink-faint">System</span>}</td>
                      <td className="px-4 py-2.5">{e.target_name && e.target_id !== e.actor_id ? e.target_name : <span className="text-ink-faint">–</span>}</td>
                      <td className="num px-4 py-2.5 text-ink-soft">{e.ip ?? '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </LoadState>
        {next && (
          <div className="border-t border-rule p-3 text-center">
            <button type="button" className="btn-quiet py-2 text-step--1" onClick={() => void more()} disabled={loadingMore}>
              {loadingMore ? 'Loading…' : 'Load older events'}
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
