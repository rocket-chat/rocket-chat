// Serve the Monaco editor from this app instead of a public CDN, so the cockpit works with no
// internet access (air-gapped installs) and under a strict Content-Security-Policy.
import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(root, "node_modules", "monaco-editor", "min", "vs");
const target = join(root, "public", "monaco", "vs");

if (!existsSync(source)) {
  console.error("monaco-editor is not installed; run `pnpm install` first.");
  process.exit(1);
}
rmSync(target, { recursive: true, force: true });
mkdirSync(dirname(target), { recursive: true });
cpSync(source, target, { recursive: true });
console.log(`Monaco assets copied to ${target}`);
