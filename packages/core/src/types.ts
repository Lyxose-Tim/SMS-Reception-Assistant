export type Locale = 'en' | 'zh-CN';

export interface ServiceItem {
  name: string;
  description: string;
  pricing: string;
}

export interface BusinessProfile {
  about: string;
  hours: string;
  serviceAreas: string[];
  services: ServiceItem[];
  priceNote: string;
  contactEmail: string;
  contactPhone: string;
  address: string;
}

export interface Faq {
  id: string;
  question: string;
  answer: string;
  enabled: boolean;
}

export type BookingFieldKind = 'text' | 'contact' | 'service';

export interface BookingFieldDef {
  key: string;
  label: Record<Locale, string>;
  kind: BookingFieldKind;
  required: boolean;
}

export interface BusinessKnowledge {
  name: string;
  profile: BusinessProfile;
  faqs: Faq[];
  bookingFields: BookingFieldDef[];
}

export interface BookingDraft {
  fields: Record<string, string>;
  /** Key of the field the assistant last asked for, if any. */
  awaiting: string | null;
  /** ISO timestamp of the last change. */
  updatedAt: string;
}

export type ConversationStatus = 'bot' | 'human' | 'closed';
export type ChatRole = 'visitor' | 'bot' | 'agent';

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ConversationState {
  status: ConversationStatus;
  locale: Locale | null;
  bookingDraft: BookingDraft | null;
  /** Visitor messages already stored before the current one. */
  visitorMessageCount: number;
}

export interface EngineContext {
  business: BusinessKnowledge;
  conversation: ConversationState;
  /** Prior messages, oldest first, excluding the current visitor message. */
  history: ChatMessage[];
  /** Browser language reported by the widget, e.g. "zh-CN" or "en-US". */
  localeHint?: string | null;
  now: Date;
}

export type HandoffReason = 'visitor_request' | 'unknown_question' | 'model_request' | 'message_limit';

export interface BookingRequestDraft {
  fields: Record<string, string>;
}

export type ModelAction =
  | { type: 'update_booking_draft'; fields: Record<string, string> }
  | { type: 'submit_booking_request' }
  | { type: 'handoff_to_human'; reason: string };

export interface ModelMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ModelInput {
  system: string;
  messages: ModelMessage[];
  signal: AbortSignal;
  maxOutputTokens: number;
}

export interface ModelOutput {
  text: string;
  actions: ModelAction[];
}

/** Port implemented by the AI adapter in apps/server. Core never imports an AI SDK. */
export interface ReplyModel {
  generate(input: ModelInput): Promise<ModelOutput>;
}

export interface EngineMeta {
  engine: 'ai' | 'rules' | 'none';
  fallbackReason?: 'no_model' | 'timeout' | 'model_error' | 'empty_reply';
  knowledgeTruncated?: boolean;
  actions?: ModelAction[];
}

export interface EngineResult {
  replies: string[];
  locale: Locale;
  bookingDraft: BookingDraft | null;
  bookingRequest?: BookingRequestDraft;
  handoff?: { reason: HandoffReason };
  meta: EngineMeta;
}

export const DEFAULT_BOOKING_FIELDS: BookingFieldDef[] = [
  { key: 'service', label: { en: 'service', 'zh-CN': '服务项目' }, kind: 'service', required: true },
  { key: 'preferred_time', label: { en: 'preferred date and time', 'zh-CN': '期望的日期和时间' }, kind: 'text', required: true },
  { key: 'name', label: { en: 'name', 'zh-CN': '姓名' }, kind: 'text', required: true },
  { key: 'contact', label: { en: 'email or phone number', 'zh-CN': '邮箱或电话' }, kind: 'contact', required: true },
  { key: 'notes', label: { en: 'notes', 'zh-CN': '备注' }, kind: 'text', required: false },
];
