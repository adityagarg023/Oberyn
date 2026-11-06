import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const bundle = readFileSync(new URL("../dist/index.js", import.meta.url));
const gzippedBytes = gzipSync(bundle, { level: 9 }).byteLength;
const limitBytes = 5 * 1024;

console.log(`@oberyn/sdk core: ${gzippedBytes} B gzipped (limit: ${limitBytes} B)`);
if (gzippedBytes >= limitBytes) {
  console.error("SDK bundle exceeds the 5 KB gzipped budget.");
  process.exitCode = 1;
}
