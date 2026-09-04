import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

test("settings layout installs independently memoized provider slices", () => {
  const layout = read("app/(utility)/settings/layout.tsx");
  for (const provider of [
    "UiSettingsProvider",
    "ModelCatalogProvider",
    "SettingsDraftProvider",
  ]) {
    assert.match(layout, new RegExp(`<${provider}>`));
  }

  const ui = read("features/settings/store/UiSettingsProvider.tsx");
  const catalog = read("features/settings/store/ModelCatalogProvider.tsx");
  const draft = read("features/settings/store/SettingsDraftProvider.tsx");
  for (const source of [ui, catalog, draft]) {
    assert.match(source, /useMemo/);
    assert.doesNotMatch(source, /\}, \[source\]\)/);
  }
});

test("appearance consumes only the UI preference slice", () => {
  const appearance = read(
    "features/settings/sections/AppearanceSettingsSection.tsx",
  );
  assert.match(appearance, /useUiSettings\(\)/);
  assert.doesNotMatch(appearance, /useSettings\(\)/);
});

test("the UI preference slice exposes the learning opt-in without the catalog surface", () => {
  // Break caught: the learning settings page must consume the full catalog store or cannot update its saved toggle.
  const ui = read("features/settings/store/UiSettingsProvider.tsx");

  for (const member of [
    "learningCoordinatorEnabled",
    "updateLearningCoordinatorEnabled",
  ]) {
    assert.match(ui, new RegExp(member));
  }

  const learning = read(
    "features/settings/sections/LearningSettingsSection.tsx",
  );
  assert.match(learning, /useUiSettings\(\)/);
  assert.doesNotMatch(learning, /useSettings\(\)/);
});

test("continuous settings imports feature sections, never route modules", () => {
  const page = read("app/(utility)/settings/page.tsx");
  assert.match(page, /features\/settings\/sections\/ModelsSettingsSection/);
  assert.match(page, /features\/settings\/sections\/ChatSettingsSection/);
  assert.doesNotMatch(page, /from "\.\/.+\/page"|from "\.\/.+page"/);
});
