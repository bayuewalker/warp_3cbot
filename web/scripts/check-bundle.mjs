import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import { gzipSync } from "node:zlib";

const dist = path.resolve("dist/assets");
// Vite prints gzip in decimal kB (bytes / 1000), not KiB.
const KB = 1000;
const ENTRY_JS_BASE = 113.01 * KB;
const ENTRY_CSS_BASE = 14.28 * KB;
const MAX_GROWTH = 1 * KB;
const OFFICE_MAX = 150 * KB;

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
const officeCss = files.filter((file) => /^(office3d|Office3D)-.*\.css$/.test(file.name));
const officeImg = files.filter((file) => /office-sprite|pixel-sheet/.test(file.name));
const entryBytes = entryJs.reduce((sum, file) => sum + file.gzip, 0);
const cssBytes = entryCss.reduce((sum, file) => sum + file.gzip, 0);
const officeBytes = [...officeJs, ...officeCss, ...officeImg].reduce((sum, file) => sum + file.gzip, 0);
const jsGrowth = entryBytes - ENTRY_JS_BASE;
const cssGrowth = cssBytes - ENTRY_CSS_BASE;

console.log("entry js  " + entryJs.map((file) => file.name + " " + kb(file.gzip)).join(", ") + "  growth " + kb(jsGrowth));
console.log("entry css " + entryCss.map((file) => file.name + " " + kb(file.gzip)).join(", ") + "  growth " + kb(cssGrowth));
const officeFiles = [...officeJs, ...officeCss, ...officeImg];
console.log("3d js     " + (officeFiles.length ? officeFiles.map((file) => file.name + " " + kb(file.gzip)).join(", ") : "(missing)") + "  total " + kb(officeBytes));

const failures = [];
if (entryJs.length !== 1) failures.push("expected one index-*.js entry chunk");
if (entryCss.length !== 1) failures.push("expected one index-*.css entry chunk");
if (!officeJs.length) failures.push("expected an Office3D-*.js or office3d-*.js chunk");
if (entryJs.some((file) => /from"\.\/office3d-/.test(file.text) || /from"\.\/Office3D-/.test(file.text))) {
  failures.push("entry statically imports the 3D chunk");
}
if (jsGrowth > MAX_GROWTH) failures.push("entry js grew " + kb(jsGrowth) + " (limit 1 kB)");
if (cssGrowth > MAX_GROWTH) failures.push("entry css grew " + kb(cssGrowth) + " (limit 1 kB)");
if (officeBytes > OFFICE_MAX) failures.push("3d chunk is " + kb(officeBytes) + " (limit 150 kB)");
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("bundle check passed");
