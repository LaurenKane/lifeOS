/**
 * Tailwind v4 configuration.
 *
 * The design tokens live in src/index.css under `@theme inline` (the
 * Tailwind v4 way — CSS is the source of truth). This file exists so
 * that tooling and shadcn can find a config, and so the source globs are
 * declared explicitly rather than relying on auto-detection.
 *
 * Note: the previous version of this file registered daisyui, but
 * daisyui was never installed, so the plugin reference was dead. It is
 * not used by the current UI and has been dropped.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
