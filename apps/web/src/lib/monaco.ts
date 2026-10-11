import { loader } from "@monaco-editor/react";

// Monaco's assets are served by this app (copied to /monaco at build time by
// scripts/copy-monaco.mjs) rather than fetched from a public CDN.
loader.config({ paths: { vs: "/monaco/vs" } });
