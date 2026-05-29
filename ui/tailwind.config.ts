import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Page chrome — keep parity with the legacy single-file UI
        bg: '#f4f4f0',
        border: '#e0e0d8',
        muted: '#666',
        accent: '#2563eb',
        // U16 — semantic status tokens. New code references these
        // names; existing bg-emerald/amber/red usage keeps working
        // until each call-site migrates. The mapping is one-to-one
        // with the corresponding Tailwind palette so the on-screen
        // result is identical.
        ready: { 50: '#ecfdf5', 100: '#d1fae5', 500: '#10b981', 600: '#059669', 700: '#047857', 900: '#064e3b' },
        warn:  { 50: '#fffbeb', 100: '#fef3c7', 500: '#f59e0b', 600: '#d97706', 700: '#b45309', 900: '#78350f' },
        flag:  { 50: '#fef2f2', 100: '#fee2e2', 500: '#ef4444', 600: '#dc2626', 700: '#b91c1c', 900: '#7f1d1d' },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        // Match legacy tile/badge/label sizes
        '2xs': '0.625rem',
        xxs: '0.6875rem',
      },
    },
  },
};

export default config;
