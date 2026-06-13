#!/usr/bin/env node
import { execFileSync } from "node:child_process";

const requiredPaths = [
  "package.json",
  "README.md",
  "CONFIGURATION.md",
  "docs/pi.md",
  "extensions/index.ts",
  "pi-skills/last30days/SKILL.md",
  "skills/last30days/SKILL.md",
  "skills/last30days/scripts/last30days.py",
  "skills/last30days/scripts/lib/schema.py",
  "skills/last30days/scripts/lib/render.py",
  "skills/last30days/scripts/lib/setup_wizard.py",
  "scripts/verify-pi-package.mjs",
];

const forbiddenPatterns = [
  /__pycache__\//,
  /\.pyc$/,
  /^\.venv\//,
  /^\.pytest_cache\//,
  /^tests\//,
  /^fixtures\//,
  /^media\//,
];

function fail(message) {
  console.error(`verify-pi-package: ${message}`);
  process.exit(1);
}

const raw = execFileSync("npm", ["pack", "--json", "--dry-run"], {
  encoding: "utf8",
  stdio: ["ignore", "pipe", "inherit"],
});

const parsed = JSON.parse(raw);
if (!Array.isArray(parsed) || parsed.length === 0) {
  fail("npm pack --json --dry-run returned no package metadata");
}

const pkg = parsed[0];
const files = Array.isArray(pkg.files) ? pkg.files.map((entry) => entry.path) : [];
const fileSet = new Set(files);

for (const requiredPath of requiredPaths) {
  if (!fileSet.has(requiredPath)) {
    fail(`missing required packaged file: ${requiredPath}`);
  }
}

for (const path of files) {
  for (const pattern of forbiddenPatterns) {
    if (pattern.test(path)) {
      fail(`forbidden packaged file matched ${pattern}: ${path}`);
    }
  }
}

if (typeof pkg.entryCount === "number" && pkg.entryCount > 220) {
  fail(`package contains ${pkg.entryCount} entries; expected a lean pi package`);
}

console.log(
  `verify-pi-package: ok (${pkg.filename}, ${pkg.entryCount ?? files.length} entries, ${pkg.unpackedSize} bytes unpacked)`,
);
