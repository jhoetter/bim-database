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
