import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

/** P5: WCAG 2.x contrast for the shipped text tokens. Read from index.css, so a palette edit is measured, not assumed. */

const css = readFileSync(new URL("./src/index.css", import.meta.url), "utf8");
const root = /:root\s*\{([\s\S]*?)\n\}/.exec(css);
assert.ok(root, "no :root token block in index.css");

const tokens = new Map<string, string>();
for (const match of root[1].matchAll(/(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g)) tokens.set(match[1], match[2].toLowerCase());

const channel = (value: number) => {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const luminance = (hex: string) => {
  const n = Number.parseInt(hex.slice(1), 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
};
const ratio = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const token = (name: string) => {
  const value = tokens.get(name);
  assert.ok(value, `missing token ${name}`);
  return value;
};

const SURFACES = ["--bg", "--bg2", "--bg3", "--bg4", "--command-surface"];
const TEXT = ["--text", "--text2", "--muted", "--muted2"];
const ACCENT = ["--primary", "--primary-2", "--success", "--success-2", "--warning", "--danger", "--danger-2", "--agent-accent"];

test("the contrast function matches the WCAG reference values", () => {
  assert.equal(ratio("#000000", "#ffffff").toFixed(2), "21.00");
  assert.equal(ratio("#777777", "#ffffff").toFixed(2), "4.48");
  assert.equal(ratio("#767676", "#ffffff").toFixed(2), "4.54");
  assert.equal(ratio("#ffffff", "#ffffff"), 1);
});

test("every surface and text token is readable as a hex literal", () => {
  for (const name of [...SURFACES, ...TEXT, ...ACCENT]) assert.match(token(name), /^#[0-9a-f]{6}$/, name);
});

test("body and muted text keep AA contrast (4.5:1) on every surface", () => {
  for (const text of TEXT) {
    for (const surface of SURFACES) {
      const value = ratio(token(text), token(surface));
      assert.ok(value >= 4.5, `${text} on ${surface} is ${value.toFixed(2)}:1`);
    }
  }
});

test("status and accent colours used as text keep AA contrast on every surface", () => {
  for (const text of ACCENT) {
    for (const surface of SURFACES) {
      const value = ratio(token(text), token(surface));
      assert.ok(value >= 4.5, `${text} on ${surface} is ${value.toFixed(2)}:1`);
    }
  }
});

test("the contrast claims written beside the tokens are not overstated", () => {
  // index.css states "8.3:1 on --bg3" for --muted and "5.6:1 on --bg3" for --muted2.
  const claims: Array<[string, number]> = [["--muted", 8.3], ["--muted2", 5.6]];
  for (const [name, claimed] of claims) {
    const measured = ratio(token(name), token("--bg3"));
    assert.ok(measured >= claimed - 0.1, `${name} is ${measured.toFixed(2)}:1, the comment says ${claimed}:1`);
  }
});

test("line tokens are decoration only: they are not claimed as text colours", () => {
  for (const name of ["--line", "--line2", "--line3"]) assert.ok(!TEXT.includes(name));
  const border = ratio(token("--line3"), token("--bg3"));
  // Reported, not gated: borders carry no information that is not also in text.
  assert.ok(border > 1, `--line3 on --bg3 is ${border.toFixed(2)}:1`);
});
