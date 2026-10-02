/**
 * PostCSS config.
 *
 * Tailwind v4 ships its own PostCSS plugin, which replaces the v3
 * `tailwind: true` form. The `tailwindcss` package alone is not
 * sufficient — without this plugin no CSS is generated at all.
 */
export default {
  plugins: {
    "@tailwindcss/postcss": {},
    autoprefixer: {},
  },
};
