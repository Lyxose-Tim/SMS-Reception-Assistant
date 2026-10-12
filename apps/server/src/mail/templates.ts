import type { HandoffReason, Locale } from '@reception/core';

export const MAX_EMAIL_FIELD_CHARS = 200;
const URL_PATTERN = /\b(?:https?:\/\/|www\.)\S+/gi;
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g;

export interface EmailContent {
  subject: string;
  text: string;
}

export interface FieldLine {
  label: string;
  value: string;
}

/** For visitor-supplied text in emails: no links, no control characters, one line, capped. */
export function sanitizeField(value: string): string {
  const clean = value.replace(URL_PATTERN, '[link removed]').replace(CONTROL_CHARS, '').replace(/\s+/g, ' ').trim();
  return clean.length > MAX_EMAIL_FIELD_CHARS ? `${clean.slice(0, MAX_EMAIL_FIELD_CHARS - 1)}…` : clean;
}

const list = (fields: FieldLine[]) => fields.map((f) => `- ${f.label}: ${sanitizeField(f.value)}`).join('\n');

const HANDOFF_REASONS: Record<Locale, Record<HandoffReason, string>> = {
  en: {
    visitor_request: 'The visitor asked for a person',
    unknown_question: "The assistant couldn't answer a question",
    model_request: "The assistant couldn't answer a question",
    message_limit: 'The conversation reached its message limit',
  },
  'zh-CN': {
    visitor_request: '访客要求人工服务',
    unknown_question: '助手无法回答访客的问题',
    model_request: '助手无法回答访客的问题',
    message_limit: '对话已达到消息上限',
  },
};

export function ownerBookingEmail(input: { businessName: string; fields: FieldLine[]; adminUrl: string }, locale: Locale): EmailContent {
  return locale === 'zh-CN'
    ? {
        subject: `【${input.businessName}】新的预约申请（待确认）`,
        text: `收到一条新的预约申请，请在后台确认或拒绝：\n\n${list(input.fields)}\n\n查看：${input.adminUrl}`,
      }
    : {
        subject: `[${input.businessName}] New booking request (pending)`,
        text: `A visitor submitted a booking request. Please confirm or decline it in the dashboard:\n\n${list(input.fields)}\n\nOpen: ${input.adminUrl}`,
      };
}

export function ownerHandoffEmail(
  input: { businessName: string; reason: HandoffReason; excerpt: string; adminUrl: string },
  locale: Locale,
): EmailContent {
  const reason = HANDOFF_REASONS[locale][input.reason];
  return locale === 'zh-CN'
    ? {
        subject: `【${input.businessName}】有访客需要人工处理`,
        text: `${reason}。\n\n访客消息：${sanitizeField(input.excerpt)}\n\n查看：${input.adminUrl}`,
      }
    : {
        subject: `[${input.businessName}] A visitor needs your attention`,
        text: `${reason}.\n\nVisitor message: ${sanitizeField(input.excerpt)}\n\nOpen: ${input.adminUrl}`,
      };
}

export function ownerOfflineContactEmail(
  input: { businessName: string; contact: FieldLine[]; note: string; adminUrl: string },
  locale: Locale,
): EmailContent {
  const note = input.note ? sanitizeField(input.note) : '';
  return locale === 'zh-CN'
    ? {
        subject: `【${input.businessName}】访客留下了联系方式`,
        text: `访客留下了联系方式，请尽快跟进：\n\n${list(input.contact)}${note ? `\n\n留言：${note}` : ''}\n\n查看：${input.adminUrl}`,
      }
    : {
        subject: `[${input.businessName}] A visitor left their contact details`,
        text: `A visitor left their contact details. Please follow up:\n\n${list(input.contact)}${note ? `\n\nNote: ${note}` : ''}\n\nOpen: ${input.adminUrl}`,
      };
}

export function visitorReceiptEmail(input: { businessName: string; fields: FieldLine[] }, locale: Locale): EmailContent {
  return locale === 'zh-CN'
    ? {
        subject: `${input.businessName}：我们已收到您的预约申请`,
        text: `感谢您的预约申请，目前正在等待确认，${input.businessName}会与您联系确认。\n\n${list(input.fields)}\n\n这是一封自动发送的邮件。`,
      }
    : {
        subject: `${input.businessName}: we received your booking request`,
        text: `Thanks for your request. It is pending confirmation — ${input.businessName} will contact you to confirm.\n\n${list(input.fields)}\n\nThis is an automated message.`,
      };
}
