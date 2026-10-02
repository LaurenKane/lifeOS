/**
 * Global test setup.
 *
 * `@testing-library/jest-dom/vitest` extends `expect` with DOM
 * matchers (toBeInTheDocument, toHaveTextContent, ...). It also
 * registers afterEach(cleanup) for RTL, so React trees do not leak
 * between tests.
 */
import "@testing-library/jest-dom/vitest";
