import { ChevronLeft, ChevronRight, KeyRound, LogOut, Mail, Search, ShieldOff, ShieldCheck } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { errorMessage } from '@/services/api';
import { useAuthStore } from '@/store/useAuthStore';
import type { Role } from '@/types';

import { adminApi, type AdminUser, type UserPage } from './adminApi';
import { absolute, relative, roleLabel } from './format';
import { Badge, LoadState, SectionHeader, useConfirm, useToast } from './ui';

const PAGE = 12;

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function StatusBadge({ user }: { user: AdminUser }) {
  if (!user.is_active) return <Badge tone="bad">Disabled</Badge>;
  if (user.locked) return <Badge tone="warn">Locked</Badge>;
  return <Badge tone="good">Active</Badge>;
}

export function UsersPanel() {
  const me = useAuthStore((s) => s.user);
  const confirm = useConfirm();
  const toast = useToast();
  const [search, setSearch] = useState('');
  const [role, setRole] = useState<Role | ''>('');
  const [state, setState] = useState('');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<UserPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const q = useDebounced(search);

  const load = useCallback(() => {
    adminApi
      .users({ search: q, role, state, limit: PAGE, offset })
      .then((p) => {
        setPage(p);
        setError(null);
      })
      .catch((err) => setError(errorMessage(err, 'Users couldn’t load.')));
  }, [q, role, state, offset]);

  useEffect(load, [load]);
  useEffect(() => setOffset(0), [q, role, state]);

  const act = async (user: AdminUser, run: () => Promise<unknown>, done: string) => {
    setBusy(user.id);
    try {
      await run();
      toast(done);
      load();
    } catch (err) {
      toast(errorMessage(err, 'That change didn’t save.'), 'bad');
    } finally {
      setBusy(null);
    }
  };

  const changeRole = async (user: AdminUser, next: Role) => {
    if (next === user.role) return;
    const ok = await confirm({
      title: `Make ${user.full_name} a ${roleLabel(next).toLowerCase()}?`,
      body:
        next === 'ADMIN'
          ? 'Admins can see every patient’s results and change any account. They’ll be signed out and must sign in again.'
          : user.role === 'CLINICIAN'
            ? 'Their care-team assignments will be removed and they’ll be signed out of every device.'
            : 'They’ll be signed out of every device and get the new permissions when they sign in again.',
      confirm: `Make ${roleLabel(next).toLowerCase()}`,
      danger: next === 'ADMIN',
    });
    if (ok) await act(user, () => adminApi.setRole(user.id, next), `${user.full_name} is now a ${roleLabel(next).toLowerCase()}.`);
  };

  const toggleActive = async (user: AdminUser) => {
    const disabling = user.is_active;
    const ok = await confirm({
      title: disabling ? `Disable ${user.full_name}’s account?` : `Enable ${user.full_name}’s account?`,
      body: disabling
        ? 'They’ll be signed out everywhere and won’t be able to sign in. Their readings and results are kept.'
        : 'They’ll be able to sign in again with their existing password.',
      confirm: disabling ? 'Disable account' : 'Enable account',
      danger: disabling,
    });
    if (ok) await act(user, () => adminApi.setActive(user.id, !disabling), disabling ? 'Account disabled.' : 'Account enabled.');
  };

  const signOut = async (user: AdminUser) => {
    const ok = await confirm({
      title: `Sign ${user.full_name} out everywhere?`,
      body: 'Every browser and device they’re signed in on will need their password again within 15 minutes, and any live session will stop.',
      confirm: 'Sign out everywhere',
      danger: true,
    });
    if (ok) await act(user, () => adminApi.signOut(user.id), `${user.full_name} was signed out everywhere.`);
  };

  const sendReset = async (user: AdminUser) => {
    const ok = await confirm({
      title: `Email ${user.full_name} a password reset link?`,
      body: `A single-use link valid for 30 minutes goes to ${user.email}. Their current password keeps working until they use it. You never see or set their password.`,
      confirm: 'Send reset link',
    });
    if (ok) await act(user, () => adminApi.sendReset(user.id), `Reset link sent to ${user.email}.`);
  };

  const pages = page ? Math.max(1, Math.ceil(page.total / PAGE)) : 1;
  const current = Math.floor(offset / PAGE) + 1;

  return (
    <section aria-labelledby="users-title">
      <SectionHeader title="Users" description="Every account, its role and its sign-in state. Role changes and account actions are recorded in the audit log." />
      <div className="panel bg-white">
        <div className="flex flex-wrap items-end gap-3 border-b border-rule p-4">
          <label className="min-w-[16rem] flex-1">
            <span className="mb-1 block text-step--1 font-medium">Search</span>
            <span className="relative block">
              <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint" aria-hidden />
              <input className="field py-2 pl-9" placeholder="Name or email" value={search} onChange={(e) => setSearch(e.target.value)} />
            </span>
          </label>
          <label>
            <span className="mb-1 block text-step--1 font-medium">Role</span>
            <select className="field py-2" value={role} onChange={(e) => setRole(e.target.value as Role | '')}>
              <option value="">All roles</option>
              <option value="PATIENT">Patients</option>
              <option value="CLINICIAN">Clinicians</option>
              <option value="ADMIN">Admins</option>
            </select>
          </label>
          <label>
            <span className="mb-1 block text-step--1 font-medium">Status</span>
            <select className="field py-2" value={state} onChange={(e) => setState(e.target.value)}>
              <option value="">Any status</option>
              <option value="active">Active</option>
              <option value="locked">Locked</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
        </div>

        <LoadState loading={!page && !error} error={error} empty={page?.items.length === 0} emptyText="No accounts match these filters.">
          {page && (
            <>
            {/* Phones: one card per person, every action visible without sideways scrolling. */}
            <ul className="divide-y divide-rule/70 text-step--1 sm:hidden">
              {page.items.map((u) => {
                const self = u.id === me?.id;
                return (
                  <li key={u.id} className="space-y-3 px-4 py-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium text-ink">{u.full_name}{self && <span className="ml-1.5 text-ink-faint">(you)</span>}</p>
                        <p className="break-all text-ink-soft">{u.email}</p>
                      </div>
                      <StatusBadge user={u} />
                    </div>
                    <dl className="grid grid-cols-2 gap-x-4 gap-y-1">
                      <div>
                        <dt className="text-ink-faint">Last sign-in</dt>
                        <dd className="text-ink">{relative(u.last_login_at)}</dd>
                      </div>
                      {u.role === 'PATIENT' && (
                        <div>
                          <dt className="text-ink-faint">Sessions</dt>
                          <dd className="num text-ink">{u.session_count}</dd>
                        </div>
                      )}
                    </dl>
                    {u.failed_login_count > 0 && u.is_active && (
                      <p className="text-ink-faint">{u.failed_login_count} failed sign-in{u.failed_login_count === 1 ? '' : 's'}</p>
                    )}
                    <label className="block">
                      <span className="mb-1 block text-ink-faint">Role</span>
                      <select
                        aria-label={`Role for ${u.full_name}`}
                        className="field py-2 text-step--1"
                        value={u.role}
                        disabled={self || busy === u.id}
                        onChange={(e) => void changeRole(u, e.target.value as Role)}
                      >
                        <option value="PATIENT">Patient</option>
                        <option value="CLINICIAN">Clinician</option>
                        <option value="ADMIN">Admin</option>
                      </select>
                    </label>
                    {!self && (
                      <div className="flex flex-wrap gap-2">
                        {u.locked && u.is_active && (
                          <button type="button" className="btn-quiet px-3 py-2 text-step--1" disabled={busy === u.id}
                            onClick={() => void act(u, () => adminApi.unlock(u.id), `${u.full_name} can sign in again.`)}>
                            <KeyRound size={15} aria-hidden /> Unlock
                          </button>
                        )}
                        {u.is_active && (
                          <button type="button" className="btn-quiet px-3 py-2 text-step--1" disabled={busy === u.id} onClick={() => void sendReset(u)}>
                            <Mail size={15} aria-hidden /> Send reset link
                          </button>
                        )}
                        <button type="button" className="btn-quiet px-3 py-2 text-step--1" disabled={busy === u.id} onClick={() => void signOut(u)}>
                          <LogOut size={15} aria-hidden /> Sign out everywhere
                        </button>
                        <button type="button" className="btn-quiet px-3 py-2 text-step--1" disabled={busy === u.id} onClick={() => void toggleActive(u)}>
                          {u.is_active ? <ShieldOff size={15} aria-hidden /> : <ShieldCheck size={15} aria-hidden />}
                          {u.is_active ? 'Disable' : 'Enable'}
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="relative hidden overflow-x-auto sm:block">
              <table className="w-full min-w-[50rem] text-left text-step--1">
                <thead className="border-b border-rule bg-paper-card text-ink-soft">
                  <tr>
                    <th scope="col" className="px-4 py-2.5 font-medium">Person</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Role</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                    <th scope="col" className="px-4 py-2.5 font-medium">Last sign-in</th>
                    <th scope="col" className="px-4 py-2.5 text-right font-medium">Sessions</th>
                    <th scope="col" className="hidden px-4 py-2.5 font-medium 2xl:table-cell">Joined</th>
                    <th scope="col" className="px-4 py-2.5 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((u) => {
                    const self = u.id === me?.id;
                    return (
                      <tr key={u.id} className="border-b border-rule/70 last:border-0 hover:bg-paper-card/60">
                        <td className="px-4 py-3">
                          <p className="font-medium text-ink">{u.full_name}{self && <span className="ml-2 text-ink-faint">(you)</span>}</p>
                          <p className="text-ink-soft">{u.email}</p>
                        </td>
                        <td className="px-4 py-3">
                          <select
                            aria-label={`Role for ${u.full_name}`}
                            className="field w-auto py-1.5 text-step--1"
                            value={u.role}
                            disabled={self || busy === u.id}
                            onChange={(e) => void changeRole(u, e.target.value as Role)}
                          >
                            <option value="PATIENT">Patient</option>
                            <option value="CLINICIAN">Clinician</option>
                            <option value="ADMIN">Admin</option>
                          </select>
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge user={u} />
                          {u.failed_login_count > 0 && u.is_active && (
                            <p className="mt-1 text-ink-faint">{u.failed_login_count} failed sign-in{u.failed_login_count === 1 ? '' : 's'}</p>
                          )}
                        </td>
                        <td className="px-4 py-3" title={absolute(u.last_login_at)}>{relative(u.last_login_at)}</td>
                        <td className="num px-4 py-3 text-right">
                          {u.role === 'PATIENT' ? u.session_count : '–'}
                          {u.role === 'PATIENT' && u.last_session_at && <p className="text-ink-faint">last {relative(u.last_session_at).toLowerCase()}</p>}
                        </td>
                        <td className="hidden px-4 py-3 2xl:table-cell" title={absolute(u.created_at)}>{relative(u.created_at)}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap justify-end gap-1">
                            {u.locked && u.is_active && (
                              <button type="button" className="btn-quiet px-2.5 py-1.5 text-step--1" disabled={busy === u.id}
                                onClick={() => void act(u, () => adminApi.unlock(u.id), `${u.full_name} can sign in again.`)}>
                                <KeyRound size={14} aria-hidden /> Unlock
                              </button>
                            )}
                            <button type="button" className="btn-quiet px-2.5 py-1.5 text-step--1" disabled={self || !u.is_active || busy === u.id}
                              onClick={() => void sendReset(u)} title="Email a password reset link">
                              <Mail size={14} aria-hidden /><span className="sr-only">Email {u.full_name} a password reset link</span>
                            </button>
                            <button type="button" className="btn-quiet px-2.5 py-1.5 text-step--1" disabled={self || busy === u.id}
                              onClick={() => void signOut(u)} title="Sign out everywhere">
                              <LogOut size={14} aria-hidden /><span className="sr-only">Sign {u.full_name} out everywhere</span>
                            </button>
                            <button type="button" className="btn-quiet px-2.5 py-1.5 text-step--1" disabled={self || busy === u.id}
                              onClick={() => void toggleActive(u)} title={u.is_active ? 'Disable account' : 'Enable account'}>
                              {u.is_active ? <ShieldOff size={14} aria-hidden /> : <ShieldCheck size={14} aria-hidden />}
                              <span className="sr-only">{u.is_active ? 'Disable' : 'Enable'} {u.full_name}</span>
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            </>
          )}
        </LoadState>

        {page && page.total > 0 && (
          <div className="flex items-center justify-between gap-3 border-t border-rule px-4 py-3 text-step--1 text-ink-soft">
            <p>
              Showing {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total}
            </p>
            <div className="flex items-center gap-2">
              <button type="button" className="btn-quiet px-2.5 py-1.5" disabled={current <= 1} onClick={() => setOffset(offset - PAGE)} aria-label="Previous page">
                <ChevronLeft size={16} aria-hidden />
              </button>
              <span className="num">Page {current} of {pages}</span>
              <button type="button" className="btn-quiet px-2.5 py-1.5" disabled={current >= pages} onClick={() => setOffset(offset + PAGE)} aria-label="Next page">
                <ChevronRight size={16} aria-hidden />
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
