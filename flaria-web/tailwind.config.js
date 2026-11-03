/** Flaria tokens: cool clinical paper, ink, scrub teal; a dark instrument inset for live signal. */
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: { DEFAULT: '#EDF1F2', deep: '#E2E8EA', card: '#F7F9F9' },
        ink: { DEFAULT: '#142028', soft: '#4A5A66', faint: '#71818C' },
        rule: '#C9D4D8',
        scrub: { DEFAULT: '#1D6B73', deep: '#14525A', wash: '#D5E7E9' },
        trace: '#19A7B5',
        stable: { DEFAULT: '#2F7A4A', wash: '#DCEDE2' },
        watch: { DEFAULT: '#C27C0E', ink: '#8A5600', wash: '#F6E7CC', hud: '#E8AE48' },
        elevated: { DEFAULT: '#B4344A', wash: '#F5DCE0', hud: '#F2808F' },
        hud: { DEFAULT: '#0F1D26', panel: '#16262F', line: '#25404C', text: '#D7E4E8', dim: '#8EA6B1' },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', '-apple-system', '"Segoe UI"', 'sans-serif'],
        display: ['"IBM Plex Sans Condensed"', '"IBM Plex Sans"', '"Arial Narrow"', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        // Modular scale (ratio 1.25) anchored at 16px.
        'step--1': ['0.8rem', { lineHeight: '1.35' }],
        'step-0': ['1rem', { lineHeight: '1.55' }],
        'step-1': ['1.25rem', { lineHeight: '1.4' }],
        'step-2': ['1.563rem', { lineHeight: '1.25' }],
        'step-3': ['1.953rem', { lineHeight: '1.15' }],
        'step-4': ['2.441rem', { lineHeight: '1.08' }],
        'step-5': ['3.815rem', { lineHeight: '0.98' }],
      },
      maxWidth: { prose: '68ch' },
      borderRadius: { instrument: '14px' },
    },
  },
  plugins: [],
};
