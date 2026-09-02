import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const EDITOR = path.resolve(
  process.cwd(),
  "components/settings/ServiceConfigEditor.tsx",
);
const CARD = path.resolve(
  process.cwd(),
  "components/settings/ClaudeCodeSubscriptionCard.tsx",
);
const EN = path.resolve(process.cwd(), "locales/en/app.json");
const ZH = path.resolve(process.cwd(), "locales/zh/app.json");

test("Claude Code settings use the local subscription card and default model", () => {
  const editor = readFileSync(EDITOR, "utf8");
  const card = readFileSync(CARD, "utf8");

  assert.match(editor, /providerValue === "claude_code"/);
  assert.match(editor, /<ClaudeCodeSubscriptionCard/);
  assert.match(editor, /provider\.value === "claude_code" \? "sonnet"/);
  assert.match(editor, /binding !== "claude_code"/);
  assert.match(card, /claude_code\.primary\.statusCommand/);
  assert.match(card, /docs\.anthropic\.com/);
});

test("Claude Code settings copy stays in sync across locales", () => {
  const en = JSON.parse(readFileSync(EN, "utf8")) as Record<string, unknown>;
  const zh = JSON.parse(readFileSync(ZH, "utf8")) as Record<string, unknown>;
  const keys = (locale: Record<string, unknown>) =>
    Object.keys(locale)
      .filter((key) => key.startsWith("claude_code.primary."))
      .sort();

  assert.deepEqual(keys(en), keys(zh));
  for (const key of keys(en)) {
    assert.equal(typeof en[key], "string");
    assert.equal(typeof zh[key], "string");
  }
});
