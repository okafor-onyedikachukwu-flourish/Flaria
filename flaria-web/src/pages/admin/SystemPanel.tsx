import { useEffect, useState } from 'react';

import { errorMessage } from '@/services/api';

import { adminApi, type SystemInfo } from './adminApi';
import { absolute, bytes, duration } from './format';
import { Badge, LoadState, SectionHeader } from './ui';

function Group({ title, rows }: { title: string; rows: [string, React.ReactNode][] }) {
  return (
    <section className="panel bg-white" aria-label={title}>
      <h2 className="border-b border-rule px-5 py-3 font-sans text-step-0 font-semibold">{title}</h2>
      <dl className="divide-y divide-rule/70">
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-4 px-5 py-2.5 text-step--1">
            <dt className="text-ink-soft">{k}</dt>
            <dd className="num text-right text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function SystemPanel() {
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.system().then(setInfo).catch((err) => setError(errorMessage(err, 'System information couldn’t load.')));
  }, []);

  return (
    <section aria-labelledby="system-title">
      <SectionHeader title="System" description="Runtime state of this API server, the analysis model and the database. Settings are read-only here; change them through the deployment environment." />
      <LoadState loading={!info && !error} error={error}>
        {info && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Group title="Service" rows={[
              ['Environment', <Badge key="e" tone={info.environment === 'production' ? 'good' : 'warn'}>{info.environment}</Badge>],
              ['API version', info.version],
              ['Uptime', duration(info.uptime_seconds)],
              ['Live streams on this server', info.live_streams],
            ]} />
            <Group title="Analysis model" rows={info.model ? [
              ['Version', info.model.version.startsWith('STUB') ? <Badge key="m" tone="warn">{info.model.version}</Badge> : info.model.version],
              ['Runs on', info.model.provider.replace('ExecutionProvider', '')],
              ['Sequence length', `${info.model.seq_len} updates`],
              ['Watch and elevated thresholds', `${info.model.threshold_watch.toFixed(2)} and ${info.model.threshold_elevated.toFixed(2)}`],
            ] : [['Status', <Badge key="x" tone="bad">Not loaded</Badge>], ['Reason', info.model_error ?? 'Unknown']]} />
            <Group title="Database" rows={[
              ['Reachable', <Badge key="r" tone={info.database.reachable ? 'good' : 'bad'}>{info.database.reachable ? 'Yes' : 'No'}</Badge>],
              ['Size', bytes(info.database.db_bytes)],
              ['Telemetry rows', info.database.telemetry_rows.toLocaleString()],
              ['Monthly telemetry partitions', info.database.telemetry_partitions],
              ['Latest telemetry', absolute(info.database.last_telemetry_at)],
              ['Signed-in devices', info.database.live_refresh_sessions],
              ['Server', info.database.server_version.split(' on ')[0]],
            ]} />
            <Group title="Telemetry writer" rows={[
              ['Rows written since start', info.telemetry.written.toLocaleString()],
              ['Waiting to be written', info.telemetry.backlog],
              ['Dropped (queue full)', info.telemetry.dropped ? <Badge key="d" tone="bad">{info.telemetry.dropped}</Badge> : 0],
              ['Consecutive write failures', info.telemetry.consecutive_failures],
            ]} />
            <Group title="Streaming and privacy" rows={[
              ['Update rate', `${info.config.tick_hz} per second`],
              ['Stored telemetry rate', `${info.config.persist_hz} per second, averages only`],
              ['Server audio buffer', `${info.config.audio_ring_seconds.toFixed(1)} s in memory, never stored`],
              ['Baseline needed before comparisons', `${info.config.min_baseline_seconds} s of signal`],
            ]} />
            <Group title="Sign-in security" rows={[
              ['Access token lifetime', `${info.config.access_token_minutes} minutes`],
              ['Refresh token lifetime', `${info.config.refresh_token_days} days, rotated on every use`],
              ['Failed sign-ins before lockout', info.config.login_max_failures],
              ['Password hashing', 'Argon2id, 64 MiB'],
            ]} />
          </div>
        )}
      </LoadState>
    </section>
  );
}
