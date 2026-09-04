"use client";

import { useTranslation } from "react-i18next";

import { Toggle } from "@/components/settings/Toggle";
import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
} from "@/components/settings/shared";
import { useUiSettings } from "@/features/settings/store";

export default function LearningSettingsSection() {
  const { t } = useTranslation();
  const { learningCoordinatorEnabled, updateLearningCoordinatorEnabled } =
    useUiSettings();

  return (
    <div>
      <SettingsPageHeader
        title={t("Learning")}
        description={t(
          "Choose whether default chats can become adaptive learning sessions.",
        )}
      />
      <SettingSection
        title={t("Learning Coordinator (Beta)")}
        description={t(
          "DeepTutor can turn a question into a focused lesson or an editable learning path.",
        )}
      >
        <SettingRow
          title={t("Use the Learning Coordinator")}
          description={t(
            "When enabled, default chats can adapt activities, hints, and review to your learning goal.",
          )}
          control={
            <Toggle
              checked={learningCoordinatorEnabled}
              label={t("Use the Learning Coordinator")}
              onChange={(next) => void updateLearningCoordinatorEnabled(next)}
            />
          }
        />
        <p className="border-t border-[var(--border)]/50 py-3.5 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
          {t(
            "Explicit capabilities, courses, Mastery, Reading, and the selection tutor still take precedence.",
          )}
        </p>
      </SettingSection>
    </div>
  );
}
