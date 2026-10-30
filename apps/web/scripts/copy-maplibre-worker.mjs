// MapLibre runs its tile work in a module worker that bundlers do not pick up: copy the worker and
// the chunk it imports to public/maplibre/ (served as-is; ClusterMap sets the worker URL to it).
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const web = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(web, "node_modules", "maplibre-gl", "dist");
const out = join(web, "public", "maplibre");
mkdirSync(out, { recursive: true });
for (const f of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) copyFileSync(join(src, f), join(out, f));
