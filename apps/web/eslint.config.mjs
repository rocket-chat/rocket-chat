import { FlatCompat } from "@eslint/eslintrc";
import { dirname } from "path";
import { fileURLToPath } from "url";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  {
    // Generated or vendored: the build copies Monaco into public/, Playwright writes its reports.
    ignores: [".next/**", "node_modules/**", "public/monaco/**", "next-env.d.ts", "test-results/**", "playwright-report/**"],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default config;
