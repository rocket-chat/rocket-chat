export interface OrgGeneralSettings {
  company_name: string;
  compliance_tier: "SOC2" | "HIPAA" | "ISO27001" | "NONE";
  session_privacy_default: "private" | "shared_org";
  telemetry_level: "minimal" | "standard" | "verbose";
  require_signed_commits: boolean;
  thrust_animation_enabled: boolean;
  suggest_next_questions: boolean;
}

export interface UserPreferencesSettings {
  full_name: string;
  avatar_url?: string;
  default_role: string;
  git_author_name: string;
  git_author_email: string;
  git_branch_prefix: string;
  git_personal_pat: string;
  slack_user_handle: string;
  slack_notifications_enabled: boolean;
  preferred_model: string;
  turbo_mode: boolean;
  theme_mode: "dark" | "light" | "system";
  notification_sound: boolean;
  suggest_next_questions: boolean;
}
