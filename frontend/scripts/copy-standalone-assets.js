#!/usr/bin/env node
/**
 * Copies the files that Next.js standalone output intentionally omits:
 *   .next/static/  →  .next/standalone/.next/static/
 *   public/        →  .next/standalone/public/
 *
 * Must run after `next build` (wired as "postbuild" in package.json).
 * Without these two directories the server.js will start and serve HTML,
 * but every /_next/static/* request will return 500.
 */
const { cpSync, existsSync } = require('fs');
const { join } = require('path');

const root = join(__dirname, '..');
const standalone = join(root, '.next', 'standalone');

if (!existsSync(standalone)) {
  console.error(
    '[copy-standalone-assets] .next/standalone not found — ' +
      'make sure output: "standalone" is set in next.config.js'
  );
  process.exit(1);
}

cpSync(
  join(root, '.next', 'static'),
  join(standalone, '.next', 'static'),
  { recursive: true, force: true }
);
console.log('[copy-standalone-assets] Copied .next/static → .next/standalone/.next/static');

cpSync(
  join(root, 'public'),
  join(standalone, 'public'),
  { recursive: true, force: true }
);
console.log('[copy-standalone-assets] Copied public/ → .next/standalone/public/');
