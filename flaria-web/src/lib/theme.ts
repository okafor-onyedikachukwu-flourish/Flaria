import type { RiskStatus } from '@/types';

/** Canvas/Chart.js colours (mirror tailwind.config.js tokens). */
export const COLORS = {
  ink: '#142028',
  inkSoft: '#4A5A66',
  rule: '#C9D4D8',
  scrub: '#1D6B73',
  trace: '#19A7B5',
  paper: '#EDF1F2',
  hud: '#0F1D26',
  hudLine: '#25404C',
  hudText: '#D7E4E8',
  hudDim: '#8EA6B1',
  watch: '#C27C0E',
  watchHud: '#E8AE48',
  elevated: '#B4344A',
  elevatedHud: '#F2808F',
  stable: '#2F7A4A',
} as const;

export const STATUS_HUD_COLOR: Record<RiskStatus, string> = {
  CALIBRATING: COLORS.hudDim,
  STABLE: COLORS.trace,
  WATCH: COLORS.watchHud,
  ELEVATED: COLORS.elevatedHud,
  SIGNAL_LOST: '#6C7F88',
};

export const STATUS_LABEL: Record<RiskStatus, string> = {
  CALIBRATING: 'Calibrating',
  STABLE: 'Stable',
  WATCH: 'Watch',
  ELEVATED: 'Elevated',
  SIGNAL_LOST: 'Signal lost',
};
