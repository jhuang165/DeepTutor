"use client";

import { CheckCircle2, ExternalLink, Terminal } from "lucide-react";
import { useTranslation } from "react-i18next";

/** Explains the local subscription-backed transport; it does not handle auth. */
export function ClaudeCodeSubscriptionCard() {
  const { t } = useTranslation();

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/20 p-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-md bg-[var(--foreground)]/10 p-1.5">
          <Terminal className="h-4 w-4 text-[var(--foreground)]" />
        </div>
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-[var(--foreground)]">
            {t("claude_code.primary.title")}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
            {t("claude_code.primary.description")}
          </p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-[12px] text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="h-3.5 w-3.5" />
          {t("claude_code.primary.localAuth")}
        </span>
        <code className="rounded bg-[var(--muted)] px-2 py-1 text-[11px] text-[var(--foreground)]">
          {t("claude_code.primary.statusCommand")}
        </code>
        <a
          href="https://docs.anthropic.com/en/docs/claude-code/getting-started"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[11.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          {t("claude_code.primary.docs")}
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-[var(--muted-foreground)]">
        {t("claude_code.primary.signInHint")}
      </p>
    </div>
  );
}
