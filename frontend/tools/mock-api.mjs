/**
 * A stand-in for the FastAPI backend that answers every read with an empty
 * result. Not part of the app: it exists so the honest EMPTY state can be looked
 * at without a migrated Postgres, which is the one state this page most needs to
 * be inspected in and the one a working backend would hide.
 *
 *   node tools/mock-api.mjs 8000
 */
import { createServer } from "node:http";

const PORT = Number(process.argv[2] ?? 8000);

const send = (res, body, status = 200) => {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
};

createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const path = url.pathname;

  if (path === "/api/v1/analytics/net-worth") return send(res, []);
  if (path === "/api/v1/analytics/spend-by-category") return send(res, []);
  if (path === "/api/v1/analytics/cashflow") return send(res, []);
  if (path === "/api/v1/accounts") return send(res, []);
  if (path === "/api/v1/accounts/types") return send(res, []);
  if (path === "/api/v1/accounts/natures") return send(res, []);
  if (path === "/api/v1/transactions") return send(res, []);
  if (path === "/api/v1/review") return send(res, []);
  if (path === "/api/v1/imports") return send(res, []);
  if (path === "/api/v1/budgets") return send(res, []);
  return send(res, { detail: `mock-api has no route for ${path}` }, 404);
}).listen(PORT, () => console.log(`mock api on :${PORT} — every read answers empty`));