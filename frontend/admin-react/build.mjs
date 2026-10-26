import { build } from "esbuild";
import { mkdir, rm, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url)),
  out = path.resolve(here, "../admin/react-assets");
await rm(out, { recursive: true, force: true });
await mkdir(out, { recursive: true });
await build({
  entryPoints: [path.join(here, "src/main.jsx")],
  outdir: out,
  entryNames: "admin",
  bundle: true,
  minify: true,
  format: "esm",
  target: ["es2020"],
  jsx: "automatic",
  legalComments: "eof",
  define: { "process.env.NODE_ENV": '"production"' },
});
let notices = "Delta X Admin — third-party notices\n";
for (const name of ["react", "react-dom", "esbuild"]) {
  try {
    notices +=
      "\n--- " +
      name +
      " ---\n" +
      (await readFile(
        path.join(here, "node_modules", name, "LICENSE"),
        "utf8",
      ));
  } catch {}
}
await writeFile(path.join(out, "THIRD_PARTY_NOTICES.txt"), notices);
console.log("Admin production assets built.");
