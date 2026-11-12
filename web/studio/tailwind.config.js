/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // 由 CSS 变量驱动（暗/亮双主题），见 styles/tokens.css
        base: "rgb(var(--c-base) / <alpha-value>)",
        surface: "rgb(var(--c-surface) / <alpha-value>)",
        raised: "rgb(var(--c-raised) / <alpha-value>)",
        overlay: "rgb(var(--c-overlay) / <alpha-value>)",
        line: "rgb(var(--c-line) / <alpha-value>)",
        "line-soft": "rgb(var(--c-line-soft) / <alpha-value>)",
        ink: "rgb(var(--c-ink) / <alpha-value>)",
        muted: "rgb(var(--c-muted) / <alpha-value>)",
        faint: "rgb(var(--c-faint) / <alpha-value>)",
        brand: "rgb(var(--c-brand) / <alpha-value>)",
        "brand-soft": "rgb(var(--c-brand-soft) / <alpha-value>)",
        magenta: "rgb(var(--c-magenta) / <alpha-value>)",
        cyan: "rgb(var(--c-cyan) / <alpha-value>)",
        success: "rgb(var(--c-success) / <alpha-value>)",
        warning: "rgb(var(--c-warning) / <alpha-value>)",
        danger: "rgb(var(--c-danger) / <alpha-value>)",
        info: "rgb(var(--c-info) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
        display: ["var(--font-display)"],
      },
      borderRadius: {
        xs: "4px",
        sm: "6px",
        md: "9px",
        lg: "13px",
        xl: "18px",
        "2xl": "24px",
      },
      // 覆盖 0–100 全部整数透明度，供 @apply 与 JIT 使用任意 /NN 修饰（如 bg-brand/12）
      opacity: Object.fromEntries(
        Array.from({ length: 101 }, (_, i) => [String(i), String(i / 100)])
      ),
      boxShadow: {
        glow: "0 0 0 1px rgb(var(--c-brand) / 0.25), 0 8px 40px -12px rgb(var(--c-brand) / 0.45)",
        panel: "0 1px 0 0 rgb(var(--c-line-soft) / 0.6) inset, 0 20px 50px -30px rgb(0 0 0 / 0.8)",
        pop: "0 24px 70px -24px rgb(0 0 0 / 0.75)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(10px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.96)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        "pulse-ring": {
          "0%": { boxShadow: "0 0 0 0 rgb(var(--c-brand) / 0.5)" },
          "70%": { boxShadow: "0 0 0 7px rgb(var(--c-brand) / 0)" },
          "100%": { boxShadow: "0 0 0 0 rgb(var(--c-brand) / 0)" },
        },
        "spin-slow": { to: { transform: "rotate(360deg)" } },
        drift: {
          "0%,100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(2%, -3%, 0) scale(1.06)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.5s cubic-bezier(0.22,1,0.36,1) both",
        "fade-in": "fade-in 0.4s ease both",
        "scale-in": "scale-in 0.22s cubic-bezier(0.22,1,0.36,1) both",
        shimmer: "shimmer 1.6s infinite",
        "pulse-ring": "pulse-ring 2s infinite",
        "spin-slow": "spin-slow 14s linear infinite",
        drift: "drift 18s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};