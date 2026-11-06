import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts", "src/integrations/browser.ts", "src/integrations/react.ts", "src/integrations/next.ts", "src/integrations/express.ts"],
  format: ["esm", "cjs"],
  dts: true,
  sourcemap: true,
  clean: true,
  target: "es2020",
  minify: true,
});
