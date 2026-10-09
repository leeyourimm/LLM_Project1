// 백엔드 응답 모양 (src/dartrag/web/*.py 와 맞춘다)

export interface Company {
  corp_code: string;
  corp_name: string;
  stock_code: string;
}

export interface AuthInfo {
  auth_required: boolean;
  allow_signup: boolean;
  // 서버에 메일 발송(SMTP)이 설정됐는지: 비밀번호 재설정, 이메일 인증에 필요
  email_enabled?: boolean;
  // 가입 이메일을 인증해야 이메일 알림을 켤 수 있는지
  email_verification_required?: boolean;
  user: { email: string; email_verified?: boolean } | null;
}

export interface Source {
  number: number | null;
  chunk_id: string;
  corp_name: string | null;
  report_nm: string | null;
  section: string;
  kind: string | null;
  body: string;
  unit: string | null;
  url: string | null;
  cited?: boolean;
}

export interface AnswerDone {
  conversation_id: number;
  message_id: number;
  question: string;
  inherited: string[];
  answer: string;
  found: boolean;
  refused: string | null;
  cached: boolean;
  warnings: string[];
  unverified_numbers: string[];
  sources: Source[];
  model: string;
  elapsed_ms: number;
  disclaimer: string;
}

export interface ConversationSummary {
  id: number;
  title: string;
  updated_at: string;
}

export interface StoredMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: Partial<AnswerDone> & { resolved_question?: string; inherited?: string[] };
  created_at: string;
  rating?: number | null;
}

export interface YearPoint {
  year: number;
  values: Record<string, number | null>;
  ratios: Record<string, number | null>;
  growth: Record<string, number | null>;
  fs_div: string | null;
  rcept_no: string | null;
}

export interface QuarterPoint {
  year: number;
  quarter: number;
  label: string;
  values: Record<string, number | null>;
  derived: string[];
}

export interface Disclosure {
  rcept_no: string;
  corp_code: string;
  corp_name: string;
  stock_code: string | null;
  report_nm: string;
  rcept_dt: string;
  event_label: string;
  importance: number;
  correction: boolean;
  url: string;
}

export interface DataIssue {
  bsns_year: number;
  reprt_code: string;
  fs_div: string;
  rule: string;
  severity: "error" | "warn";
  detail: string;
}

export interface CompanyDetail extends Company {
  watched: boolean;
  series: YearPoint[];
  disclosures: Disclosure[];
  issues: DataIssue[];
  disclaimer: string;
}

export interface CompareResult {
  year: number | null;
  companies: (Company & { point: YearPoint | null; series: YearPoint[] })[];
  disclaimer: string;
}

export interface DiffSection {
  key: string;
  status: "added" | "removed" | "changed";
  importance: number;
  added: string[];
  removed: string[];
  modified: { before: string; after: string }[];
  numbers_only: number;
}

export interface DiffResult {
  title: string;
  old: { rcept_no: string; report_nm: string };
  new: { rcept_no: string; report_nm: string };
  sections: DiffSection[];
}

export interface SummaryPoint {
  text: string;
  refs: number[];
  unverified: string[];
}

export interface DiffDigest {
  corp_name: string;
  old: { rcept_no: string; report_nm: string };
  new: { rcept_no: string; report_nm: string };
  model: string | null;
  points: Record<string, SummaryPoint[]>;
  evidence: { number: number; section: string; kind: string; text: string }[];
  metrics: { key: string; label: string; before: number | null; after: number | null; growth: number | null }[];
  dropped: number;
  sections_changed: number;
  titles: Record<string, string>;
  old_url: string;
  new_url: string;
  disclaimer: string;
}

export interface WatchItem {
  corp_code: string;
  corp_name: string;
  stock_code: string;
  min_importance: number;
}

export interface AlertChannel {
  kind: "email" | "telegram";
  target: string | null;
  verified: boolean;
  enabled: boolean;
  pending: boolean | null;
}

export interface AlertSettings {
  per_user: boolean;
  available: { email: boolean; telegram: boolean };
  channels: AlertChannel[];
  // 가입 이메일 인증 전이라 이메일 알림을 켤 수 없음
  email_needs_verification?: boolean;
}

export const METRIC_LABEL: Record<string, string> = {
  revenue: "매출액",
  operating_income: "영업이익",
  net_income: "당기순이익",
  total_liabilities: "부채총계",
  total_equity: "자본총계",
  operating_margin: "영업이익률",
  net_margin: "순이익률",
  debt_ratio: "부채비율",
};

export const IMPORTANCE: Record<number, { label: string; tone: string }> = {
  3: { label: "매우 중요", tone: "critical" },
  2: { label: "중요", tone: "warning" },
  1: { label: "참고", tone: "muted" },
};
