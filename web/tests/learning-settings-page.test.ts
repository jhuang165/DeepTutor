import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

test("learning settings: the provider hydrates and atomically updates the personal opt-in", () => {
  // Break caught: the saved backend value never reaches chat/settings state, or a failed update stays optimistic.
  const source = read("features/settings/store/SettingsStore.tsx");

  assert.match(
    source,
    /setLearningCoordinatorEnabled\(\s*payload\.ui\.learning_coordinator_enabled === true,?\s*\)/,
  );
  assert.match(source, /const updateLearningCoordinatorEnabled = useCallback/);
  assert.match(
    source,
    /persistUiSettingsPatch\(\{ learning_coordinator_enabled: next \}\)/,
  );
  assert.match(source, /setLearningCoordinatorEnabled\(previous\)/);
});

test("learning settings: Chat mounts a non-admin Learning leaf backed by interface settings", () => {
  // Break caught: the personal beta control is missing, hidden behind admin access, or reports the wrong storage owner.
  const componentPath = path.resolve(
    process.cwd(),
    "features/settings/sections/LearningSettingsSection.tsx",
  );
  assert.equal(fs.existsSync(componentPath), true);

  const chat = read("features/settings/sections/ChatSettingsSection.tsx");
  const navigation = read("features/settings/navigation/settings-nav.ts");
  assert.match(chat, /key: "learning"/);
  assert.match(navigation, /key: "learning"/);
  assert.match(
    navigation,
    /"\/settings#learning": "data\/user\/settings\/interface\.json"/,
  );
  assert.match(navigation, /learning: "data\/user\/settings\/interface\.json"/);
});
