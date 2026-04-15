/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        neu: {
          bg: '#2a2a3c',
          dark: '#1f1f2e',
          light: '#35354a',
          text: '#e0e0f0',
          dim: '#8888a8',
          accent: '#7aa2f7',
        },
      },
      boxShadow: {
        'neu-raised': '6px 6px 12px #1f1f2e, -6px -6px 12px #35354a',
        'neu-hover': '3px 3px 6px #1f1f2e, -3px -3px 6px #35354a',
        'neu-pressed': 'inset 4px 4px 8px #1f1f2e, inset -4px -4px 8px #35354a',
        'neu-inset': 'inset 4px 4px 8px #1f1f2e, inset -4px -4px 8px #35354a',
        'neu-sm': '4px 4px 8px #1f1f2e, -4px -4px 8px #35354a',
        'neu-sm-hover': '2px 2px 4px #1f1f2e, -2px -2px 4px #35354a',
        'neu-dot': 'inset 2px 2px 4px #1f1f2e, inset -2px -2px 4px #35354a',
      },
      borderRadius: {
        neu: '16px',
        'neu-sm': '12px',
        'neu-xs': '8px',
      },
    },
  },
  plugins: [],
}
