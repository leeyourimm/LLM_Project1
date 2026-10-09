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

// 위치([시작, 끝))는 백엔드(파이썬) 문자열 기준이라 유니코드 코드 포인트 단위다 (lib/passage.ts)
export type Span = [number, number];

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
  rcept_no?: string | null;
  rcept_dt?: string | null;
  // 모델이 읽은 앞뒤 문단을 포함한 원문과, 그 안에서 인용한 문단(body)의 위치. 없으면 body 전체가 인용 문단
  context?: string | null;
  highlight?: Span | null;
  // 답변에 옮긴 숫자가 원문(context 또는 body)에서 있는 위치 (답변이 끝난 뒤에만)
  quoted?: Span[];
}

export interface FilingRef {
  rcept_no: string;
  report_nm: string | null;
  rcept_dt: string | null;
}

// 답변 신뢰도: 점수가 아니라 서비스가 확인한 사실 (src/dartrag/answer/trust.py)
export interface Trust {
  sources: number; // 답변이 인용한 서로 다른 출처 수
  filings: number; // 그 출처들이 나온 서로 다른 공시 수
  numbers_checked: number; // 인용한 원문과 대조한 숫자 수
  unverified_numbers: string[];
  invalid_citations: number[];
  uncited: boolean;
  newest: (FilingRef & { corp_name: string | null; age_days: number }) | null;
  companies: {
    corp_code: string;
    corp_name: string;
    cited: FilingRef;
    latest: (FilingRef & { indexed: boolean }) | null;
    is_latest: boolean | null; // null: 비교할 정기공시를 모름
  }[];
  checked_on: string;
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
  trust: Trust | null;
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
  kind: "email" | "telegram" | "push";
  target: string | null;
  verified: boolean;
  enabled: boolean;
  pending: boolean | null;
}

// 웹 푸시를 구독한 브라우저. 구독 주소 대신 주소의 SHA-256(key)만 온다
export interface PushDevice {
  id: number;
  label: string;
  key: string;
  created_at: string;
  last_sent_at: string | null;
}

export interface AlertSettings {
  per_user: boolean;
  available: { email: boolean; telegram: boolean; push?: boolean };
  channels: AlertChannel[];
  // 가입 이메일 인증 전이라 이메일 알림을 켤 수 없음
  email_needs_verification?: boolean;
  // 웹 푸시 서버 공개키(VAPID, applicationServerKey)와 구독한 브라우저 목록
  push_public_key?: string | null;
  push_devices?: PushDevice[];
}

export interface TwoFactorStatus {
  enabled: boolean;
  recovery_codes_left: number;
}

export interface TwoFactorSetup {
  secret: string; // 4글자씩 띄운 직접 입력용 키
  otpauth_uri: string;
  qr: string; // data:image/svg+xml
  issuer: string;
  account: string;
}

// 로그인·비밀번호 재설정 응답: 2단계 인증을 켠 계정이면 user 대신 two_factor
export interface LoginResult {
  user?: { email: string; email_verified?: boolean };
  two_factor?: boolean;
  method?: "totp" | "recovery";
  recovery_codes_left?: number;
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
