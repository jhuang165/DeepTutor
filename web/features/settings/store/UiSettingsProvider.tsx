"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useSettings, type SettingsContextValue } from "./SettingsStore";

type UiSettingsSlice = Pick<
  SettingsContextValue,
  | "theme"
  | "language"
  | "responseLanguage"
  | "codeBlockTheme"
  | "codeBlockShowLineNumbers"
  | "codeBlockWrapLongLines"
  | "learningCoordinatorEnabled"
  | "updateTheme"
  | "updateLanguage"
  | "updateResponseLanguage"
  | "updateCodeBlockTheme"
  | "updateCodeBlockShowLineNumbers"
  | "updateCodeBlockWrapLongLines"
  | "updateLearningCoordinatorEnabled"
>;

const UiSettingsContext = createContext<UiSettingsSlice | null>(null);

export function UiSettingsProvider({ children }: { children: ReactNode }) {
  const source = useSettings();
  const value = useMemo<UiSettingsSlice>(
    () => ({
      theme: source.theme,
      language: source.language,
      responseLanguage: source.responseLanguage,
      codeBlockTheme: source.codeBlockTheme,
      codeBlockShowLineNumbers: source.codeBlockShowLineNumbers,
      codeBlockWrapLongLines: source.codeBlockWrapLongLines,
      learningCoordinatorEnabled: source.learningCoordinatorEnabled,
      updateTheme: source.updateTheme,
      updateLanguage: source.updateLanguage,
      updateResponseLanguage: source.updateResponseLanguage,
      updateCodeBlockTheme: source.updateCodeBlockTheme,
      updateCodeBlockShowLineNumbers: source.updateCodeBlockShowLineNumbers,
      updateCodeBlockWrapLongLines: source.updateCodeBlockWrapLongLines,
      updateLearningCoordinatorEnabled: source.updateLearningCoordinatorEnabled,
    }),
    [
      source.theme,
      source.language,
      source.responseLanguage,
      source.codeBlockTheme,
      source.codeBlockShowLineNumbers,
      source.codeBlockWrapLongLines,
      source.learningCoordinatorEnabled,
      source.updateTheme,
      source.updateLanguage,
      source.updateResponseLanguage,
      source.updateCodeBlockTheme,
      source.updateCodeBlockShowLineNumbers,
      source.updateCodeBlockWrapLongLines,
      source.updateLearningCoordinatorEnabled,
    ],
  );
  return (
    <UiSettingsContext.Provider value={value}>
      {children}
    </UiSettingsContext.Provider>
  );
}

export function useUiSettings(): UiSettingsSlice {
  const value = useContext(UiSettingsContext);
  if (!value)
    throw new Error("useUiSettings must be used inside UiSettingsProvider");
  return value;
}
