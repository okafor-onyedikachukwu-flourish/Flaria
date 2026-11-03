import { LoaderCircle, LogOut, Mic, MicOff, Play, Square } from 'lucide-react';
import axios from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { AnomalyChart, BiomarkerSparklines } from '@/components/BiomarkerCharts';
import { BrandMark } from '@/components/BrandMark';
import { DemoBanner } from '@/components/DemoBanner';
import { CareNotes } from '@/components/CareNotes';
import { LiveCameraHUD } from '@/components/LiveCameraHUD';
import { PrivacyBadge } from '@/components/PrivacyBadge';
import { ReferencePanel } from '@/components/ReferencePanel';
import { SessionHistory } from '@/components/SessionHistory';
import { SessionReport } from '@/components/SessionReport';
import { StatusConsole } from '@/components/StatusConsole';
import { useAudioCapture } from '@/hooks/useAudioCapture';
import { useFlariaWebSocket } from '@/hooks/useFlariaWebSocket';
import { encodeAudio, epochMs } from '@/lib/protocol';
import { api, errorMessage } from '@/services/api';
import { useAuthStore } from '@/store/useAuthStore';
import { useTelemetryStore } from '@/store/useTelemetryStore';
import type { PatientSummary, SessionReport as Report } from '@/types';
import { useDocumentTitle } from '@/lib/useDocumentTitle';

const READING_PASSAGE =
  'Every morning the baker opens the shop at six, sweeps the step, and sets fresh bread by the window. ' +
  'People walking past to the station often stop, buy a warm roll, and talk about the weather. ' +
  'On cold days the line reaches the corner, but nobody seems to mind the wait.';

const CONNECTION_TEXT = {
  idle: 'Not connected',
  connecting: 'Connecting…',
  open: 'Streaming',
  reconnecting: 'Reconnecting…',
  closed: 'Session ended',
  failed: 'Stopped',
} as const;

export default function Dashboard() {
  useDocumentTitle('Live session: Flaria');
  const user = useAuthStore((s) => s.user)!;
  const logout = useAuthStore((s) => s.logout);
  const connection = useTelemetryStore((s) => s.connection);
  const connectionDetail = useTelemetryStore((s) => s.connectionDetail);
  const resetTelemetry = useTelemetryStore((s) => s.reset);

  const canChoosePatient = user.role !== 'PATIENT';
  const [patients, setPatients] = useState<PatientSummary[]>([]);
  const [patientsError, setPatientsError] = useState<string | null>(null);
  const [patientId, setPatientId] = useState<string | null>(null);
  const [active, setActive] = useState(false);
  const [historyKey, setHistoryKey] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [reportState, setReportState] = useState<'idle' | 'loading' | 'waiting'>('idle');
  const audioSeq = useRef(0);
  const resultRef = useRef<HTMLDivElement>(null);
  const selectedPatient = patients.find((p) => p.id === patientId);
  const canWriteNotes = canChoosePatient && patientId !== null;

  const loadLatest = useCallback(async () => {
    setReportState('loading');
    try {
      const { data } = await api.get<Report>('/reports/latest', { params: patientId ? { patient_id: patientId } : {} });
      setReport(data);
    } catch (err) {
      if (!(axios.isAxiosError(err) && err.response?.status === 404)) console.warn('[dashboard] latest result failed', err);
      setReport(null);
    } finally {
      setReportState('idle');
    }
  }, [patientId]);

  useEffect(() => {
    if (!active) void loadLatest();
  }, [active, loadLatest]);

  /** The result is written as the server closes the session; poll briefly for it. */
  const awaitResult = useCallback(async (sessionId: string) => {
    setReportState('waiting');
    for (let attempt = 0; attempt < 10; attempt += 1) {
      await new Promise((r) => setTimeout(r, 700));
      try {
        const { data } = await api.get<Report>(`/sessions/${sessionId}/report`);
        setReport(data);
        setReportState('idle');
        setHistoryKey((k) => k + 1);
        requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
        return;
      } catch (err) {
        if (!(axios.isAxiosError(err) && err.response?.status === 404)) break;
      }
    }
    setReportState('idle');
    setHistoryKey((k) => k + 1);
  }, []);

  const viewResult = useCallback(async (sessionId: string) => {
    try {
      const { data } = await api.get<Report>(`/sessions/${sessionId}/report`);
      setReport(data);
      requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
    } catch (err) {
      console.warn('[dashboard] result unavailable', errorMessage(err));
    }
  }, []);

  useEffect(() => {
    if (!canChoosePatient) return;
    api
      .get<PatientSummary[]>('/patients')
      .then(({ data }) => setPatients(data))
      .catch((err) => setPatientsError(errorMessage(err, 'The patient list couldn’t load.')));
  }, [canChoosePatient]);

  const { send } = useFlariaWebSocket({ enabled: active, patientId });

  const onAudio = useCallback(
    (pcm: Float32Array) => {
      // Timestamp the chunk at its first sample (100 ms before it was completed).
      send(encodeAudio(pcm, audioSeq.current++, epochMs() - (pcm.length / 16_000) * 1000), 'audio');
    },
    [send],
  );
  const audio = useAudioCapture({ active, onChunk: onAudio });
  const onFrame = useCallback((frame: ArrayBuffer) => void send(frame, 'video'), [send]);

  // A terminal stream failure stops capture but keeps the explanation on screen.
  useEffect(() => {
    if (connection === 'failed') setActive(false);
  }, [connection]);

  const start = () => {
    resetTelemetry();
    audioSeq.current = 0;
    setActive(true);
  };
  const stop = () => {
    const sessionId = useTelemetryStore.getState().hello?.session_id;
    setActive(false);
    if (sessionId) void awaitResult(sessionId);
    else setTimeout(() => setHistoryKey((k) => k + 1), 1500);
  };

  const busy = active && (connection === 'connecting' || connection === 'reconnecting');

  return (
    <div className="min-h-dvh bg-paper">
      <header className="sticky top-0 z-20 border-b border-rule bg-paper/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-5 py-3">
          <BrandMark to="/app" />
          <div className="flex items-center gap-3">
            <PrivacyBadge />
            <Link to="/account" className="hidden rounded-md px-2 py-1 text-right text-step--1 leading-tight hover:bg-paper-deep sm:block" title="Account and password">
              <p className="font-medium">{user.full_name}</p>
              <p className="text-ink-soft">{user.role.charAt(0) + user.role.slice(1).toLowerCase()}</p>
            </Link>
            {user.role === 'ADMIN' && (
              <Link to="/admin" className="btn-quiet px-3 py-2 text-step--1">Admin console</Link>
            )}
            <button type="button" onClick={() => void logout()} className="btn-quiet px-3 py-2 text-step--1">
              <LogOut size={16} aria-hidden /> Sign out
            </button>
          </div>
        </div>
      </header>

      <DemoBanner />
      <main className="mx-auto max-w-7xl space-y-6 px-5 py-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-step-3 font-semibold">Live session</h1>
            <p className="mt-1 text-step--1 text-ink-soft" aria-live="polite">
              {CONNECTION_TEXT[connection]}
              {connection === 'open' && audio.state !== 'running' && audio.state !== 'starting' ? ', camera only' : ''}
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            {canChoosePatient && (
              <label className="text-step--1">
                <span className="mb-1 block font-medium">Patient</span>
                <select
                  className="field min-w-[14rem] py-2"
                  value={patientId ?? ''}
                  disabled={active}
                  onChange={(e) => setPatientId(e.target.value || null)}
                >
                  <option value="">Myself ({user.full_name})</option>
                  {patients.map((p) => (
                    <option key={p.id} value={p.id}>{p.full_name}</option>
                  ))}
                </select>
              </label>
            )}
            {active ? (
              <button type="button" onClick={stop} className="btn-danger">
                <Square size={16} aria-hidden /> Stop session
              </button>
            ) : (
              <button type="button" onClick={start} className="btn-primary">
                <Play size={16} aria-hidden /> Start session
              </button>
            )}
          </div>
        </div>

        {(connection === 'failed' || (connectionDetail && connection === 'reconnecting')) && connectionDetail && (
          <p role="alert" className={`rounded-md border px-4 py-3 text-step--1 ${connection === 'failed' ? 'border-elevated/30 bg-elevated-wash text-elevated' : 'border-watch/40 bg-watch-wash text-watch-ink'}`}>
            {connectionDetail}
          </p>
        )}
        {patientsError && <p role="alert" className="text-step--1 text-elevated">{patientsError}</p>}
        {audio.error && (
          <p role="alert" className="flex items-start gap-2 rounded-md border border-watch/40 bg-watch-wash px-4 py-3 text-step--1 text-watch-ink">
            <MicOff size={16} className="mt-0.5 shrink-0" aria-hidden />
            {audio.error} Face measures continue without voice.
          </p>
        )}

        {!active && reportState === 'waiting' && (
          <p className="panel flex items-center gap-2 p-4 text-step--1 text-ink-soft" role="status">
            <LoaderCircle size={16} className="animate-spin" aria-hidden /> Preparing the result of this reading…
          </p>
        )}
        {!active && report && (
          <div ref={resultRef} className="scroll-mt-24">
            <SessionReport report={report} onChange={setReport} canWriteNotes={canWriteNotes} patientName={selectedPatient?.full_name} />
          </div>
        )}
        {!active && !report && reportState === 'idle' && canWriteNotes && (
          <section className="panel p-5" aria-labelledby="notes-heading">
            <h2 id="notes-heading" className="mb-3 text-step-1 font-semibold">Care team notes for {selectedPatient?.full_name ?? 'this patient'}</h2>
            <CareNotes patientId={patientId!} canWrite patientName={selectedPatient?.full_name} />
          </section>
        )}

        <div className="grid gap-6 lg:grid-cols-12">
          <div className="space-y-6 lg:col-span-7">
            <LiveCameraHUD active={active} onFrame={onFrame} />
            {active && (
              <section className="panel p-5" aria-labelledby="passage-heading">
                <h2 id="passage-heading" className="flex items-center gap-2 font-sans text-step-0 font-semibold">
                  {audio.state === 'running' ? <Mic size={16} className="text-scrub" aria-hidden /> : <LoaderCircle size={16} className="animate-spin" aria-hidden />}
                  Read this aloud at a comfortable pace
                </h2>
                <p className="mt-3 max-w-prose font-sans text-step-1 leading-relaxed">{READING_PASSAGE}</p>
                <p className="mt-3 text-step--1 text-ink-soft">Repeat it until calibration finishes. The same passage each day keeps readings comparable.</p>
              </section>
            )}
            <AnomalyChart />
          </div>
          <div className="lg:col-span-5">
            <StatusConsole streaming={active && !busy} />
          </div>
        </div>

        <BiomarkerSparklines />
        <SessionHistory patientId={patientId} refreshKey={historyKey} onView={(id) => void viewResult(id)} />
        <ReferencePanel />

        <p className="pb-6 text-step--1 text-ink-faint">
          Flaria supports monitoring and does not diagnose. For sudden facial drooping, slurred speech or trouble breathing, call emergency services now.
        </p>
      </main>
    </div>
  );
}
