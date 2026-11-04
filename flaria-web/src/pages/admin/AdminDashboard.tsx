/**
 * Admin console: overview, users, care teams, escalations, audit log and system state.
 * Each section is addressable by URL hash (#users, #audit, ...) so links and the
 * browser back button work. Visible only to ADMIN accounts (route guard + API).
 */
import { Activity, ClipboardList, FlaskConical, Gauge, KeyRound, LogOut, MonitorPlay, ServerCog, ShieldAlert, Users, UsersRound } from 'lucide-react';
import { type ReactNode, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { BrandMark } from '@/components/BrandMark';
import { DemoBanner } from '@/components/DemoBanner';
import { useAuthStore } from '@/store/useAuthStore';

import { adminApi } from './adminApi';
import { AuditPanel } from './AuditPanel';
import { CareTeamPanel } from './CareTeamPanel';
import { EscalationsPanel } from './EscalationsPanel';
import { OverviewPanel } from './OverviewPanel';
import { ResearchPanel } from './ResearchPanel';
import { SystemPanel } from './SystemPanel';
import { AdminUiProvider } from './ui';
import { UsersPanel } from './UsersPanel';

const SECTIONS: { id: string; label: string; icon: ReactNode }[] = [
  { id: 'overview', label: 'Overview', icon: <Gauge size={18} aria-hidden /> },
  { id: 'users', label: 'Users', icon: <Users size={18} aria-hidden /> },
  { id: 'care-teams', label: 'Care teams', icon: <UsersRound size={18} aria-hidden /> },
  { id: 'escalations', label: 'Escalations', icon: <ShieldAlert size={18} aria-hidden /> },
  { id: 'research', label: 'Research', icon: <FlaskConical size={18} aria-hidden /> },
  { id: 'audit', label: 'Audit log', icon: <ClipboardList size={18} aria-hidden /> },
  { id: 'system', label: 'System', icon: <ServerCog size={18} aria-hidden /> },
];

export default function AdminDashboard() {
  const user = useAuthStore((s) => s.user)!;
  const logout = useAuthStore((s) => s.logout);
  const location = useLocation();
  const navigate = useNavigate();
  const requested = location.hash.replace('#', '');
  const section = SECTIONS.some((s) => s.id === requested) ? requested : 'overview';
  const [openEscalations, setOpenEscalations] = useState<number | null>(null);

  useEffect(() => {
    const load = () => adminApi.overview().then((o) => setOpenEscalations(o.open_escalations)).catch(() => undefined);
    void load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    document.title = `${SECTIONS.find((s) => s.id === section)?.label ?? 'Admin'}: Flaria admin`;
    window.scrollTo(0, 0);
  }, [section]);

  const go = (id: string) => navigate({ hash: id });

  return (
    <AdminUiProvider>
      <div className="min-h-dvh bg-paper lg:grid lg:grid-cols-[16.5rem_minmax(0,1fr)]">
        {/* The column stretches with the page; only its contents stick to the viewport. */}
        <aside className="hud bg-hud text-hud-text">
          <div className="flex flex-col lg:sticky lg:top-0 lg:h-dvh">
          <div className="flex items-center justify-between gap-3 px-5 py-5">
            <div>
              <BrandMark to="/admin" tone="light" />
              <p className="mt-1 pl-[3.1rem] text-step--1 text-hud-dim">Admin console</p>
            </div>
          </div>
          <nav aria-label="Admin sections" className="relative flex gap-1 overflow-x-auto px-3 pb-3 lg:flex-1 lg:flex-col lg:overflow-visible lg:pb-0">
            {SECTIONS.map((s) => {
              const active = s.id === section;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => go(s.id)}
                  aria-current={active ? 'page' : undefined}
                  className={`relative flex shrink-0 items-center gap-3 rounded-md px-3 py-2.5 text-left text-step--1 font-medium transition-colors ${
                    active ? 'bg-hud-panel text-white' : 'text-hud-dim hover:bg-hud-panel/60 hover:text-hud-text'
                  }`}
                >
                  {active && <span className="absolute inset-y-2 left-0 w-0.5 rounded bg-trace" aria-hidden />}
                  {s.icon}
                  <span className="flex-1">{s.label}</span>
                  {s.id === 'escalations' && openEscalations ? (
                    <span className="num rounded-full bg-elevated px-2 py-0.5 text-[0.7rem] font-semibold text-white" aria-label={`${openEscalations} awaiting follow-up`}>
                      {openEscalations}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </nav>
          <div className="hidden space-y-3 border-t border-hud-line px-5 py-5 lg:block">
            <Link to="/app" className="flex items-center gap-2 text-step--1 text-hud-dim hover:text-hud-text">
              <MonitorPlay size={16} aria-hidden /> Live session workstation
            </Link>
            <Link to="/account" className="flex items-center gap-2 text-step--1 text-hud-dim hover:text-hud-text">
              <KeyRound size={16} aria-hidden /> Account and password
            </Link>
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-scrub font-display text-step-0 font-semibold text-white" aria-hidden>
                {user.full_name.replace(/[^A-Za-z]/g, '').slice(0, 2).toUpperCase() || 'A'}
              </span>
              <div className="min-w-0">
                <p className="truncate text-step--1 font-semibold text-white">{user.full_name}</p>
                <p className="truncate text-step--1 text-hud-dim">{user.email}</p>
              </div>
            </div>
            <button type="button" onClick={() => void logout()} className="flex items-center gap-2 text-step--1 text-hud-dim hover:text-hud-text">
              <LogOut size={16} aria-hidden /> Sign out
            </button>
          </div>
          </div>
        </aside>

        <div className="min-w-0">
          <header className="flex flex-wrap items-center justify-between gap-3 border-b border-rule bg-paper/90 px-5 py-3 backdrop-blur lg:px-10">
            <p className="flex items-center gap-2 text-step--1 text-ink-soft">
              <Activity size={15} className="text-scrub" aria-hidden /> Signed in as <span className="font-semibold text-ink">{user.full_name}</span>
              <span className="hidden sm:inline">({user.email})</span>
            </p>
            <div className="flex items-center gap-2 lg:hidden">
              <Link to="/app" className="btn-quiet px-3 py-1.5 text-step--1">Workstation</Link>
              <Link to="/account" className="btn-quiet px-3 py-1.5 text-step--1">Account</Link>
              <button type="button" onClick={() => void logout()} className="btn-quiet px-3 py-1.5 text-step--1">Sign out</button>
            </div>
          </header>
          <DemoBanner />
          <main className="mx-auto max-w-[88rem] px-5 py-8 lg:px-10">
            {section === 'overview' && <OverviewPanel onNavigate={go} />}
            {section === 'users' && <UsersPanel />}
            {section === 'care-teams' && <CareTeamPanel />}
            {section === 'escalations' && <EscalationsPanel />}
            {section === 'research' && <ResearchPanel />}
            {section === 'audit' && <AuditPanel />}
            {section === 'system' && <SystemPanel />}
          </main>
        </div>
      </div>
    </AdminUiProvider>
  );
}
