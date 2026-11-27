import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";

const dist = path.resolve("dist/assets");
const KB = 1024;
// gzipSync sizes of the entry chunks on main at f875695 (the #44 room), measured in kB as bytes/1024.
const ENTRY_JS_BASE = 114232;
const ENTRY_CSS_BASE = 14379;
const MAX_GROWTH = 2 * KB;

function kb(bytes) {
  return (bytes / KB).toFixed(2) + " kB";
}

const names = await readdir(dist);
const files = [];
for (const name of names) {
  const full = path.join(dist, name);
  if (!(await stat(full)).isFile()) continue;
  const buf = await readFile(full);
  files.push({ name, gzip: gzipSync(buf).length, text: name.endsWith(".js") ? buf.toString("utf8") : "" });
}

const entryJs = files.filter((file) => /^index-.*\.js$/.test(file.name));
const entryCss = files.filter((file) => /^index-.*\.css$/.test(file.name));
const officeJs = files.filter((file) => /^(office3d|Office3D)-.*\.js$/.test(file.name));
const entryBytes = entryJs.reduce((sum, file) => sum + file.gzip, 0);
const cssBytes = entryCss.reduce((sum, file) => sum + file.gzip, 0);
const jsGrowth = entryBytes - ENTRY_JS_BASE;
const cssGrowth = cssBytes - ENTRY_CSS_BASE;

console.log("entry js  " + entryJs.map((file) => file.name + " " + kb(file.gzip)).join(", ") + "  growth " + kb(jsGrowth));
console.log("entry css " + entryCss.map((file) => file.name + " " + kb(file.gzip)).join(", ") + "  growth " + kb(cssGrowth));

const failures = [];
if (entryJs.length !== 1) failures.push("expected one index-*.js entry chunk");
if (entryCss.length !== 1) failures.push("expected one index-*.css entry chunk");
if (officeJs.length) failures.push("production must not emit a 3D office chunk");
if (entryJs.some((file) => /from"\.\/room-alive-/.test(file.text))) {
  failures.push("entry statically imports the room overlay chunk");
}
if (entryJs.some((file) => file.text.includes("Candidate observed"))) {
  failures.push("entry contains room-life copy; keep that module in the overlay chunk");
}
if (jsGrowth > MAX_GROWTH) failures.push("entry js grew " + kb(jsGrowth) + " (limit 2 kB)");
if (cssGrowth > MAX_GROWTH) failures.push("entry css grew " + kb(cssGrowth) + " (limit 2 kB)");
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("bundle check passed");
