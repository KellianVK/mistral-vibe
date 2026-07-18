import { readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const lock = JSON.parse(await readFile(join(webRoot, "package-lock.json"), "utf8"));
const sections = [];

for (const [packagePath, metadata] of Object.entries(lock.packages)) {
  if (!packagePath.startsWith("node_modules/") || metadata.dev) continue;
  const packageRoot = join(webRoot, packagePath);
  const packageJson = JSON.parse(
    await readFile(join(packageRoot, "package.json"), "utf8"),
  );
  const licenseNames = ["LICENSE", "LICENSE.md", "LICENSE.txt", "license"];
  let licenseText = null;
  for (const licenseName of licenseNames) {
    try {
      licenseText = await readFile(join(packageRoot, licenseName), "utf8");
      break;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
  if (!licenseText) {
    throw new Error(`Missing license text for ${packageJson.name}`);
  }
  sections.push(
    `${"=".repeat(78)}\n${packageJson.name}@${packageJson.version}\n${"=".repeat(78)}\n${licenseText.trim()}\n`,
  );
}

sections.sort();
await writeFile(
  resolve(webRoot, "../static/THIRD_PARTY_LICENSES.txt"),
  sections.join("\n"),
  "utf8",
);
