import type { Locale } from './types';

const en = {
  greeting_menu:
    'Hi! I can help with our hours, service areas, services and pricing, or booking a visit. What would you like to know?',
  answer_hours: 'Our hours: {hours}',
  answer_areas: 'We serve: {areas}.',
  answer_services: 'Our services: {list}',
  answer_pricing: '{list}. {note}',
  booking_ask_field: 'Could you share your {label}?',
  booking_ask_service: 'Which service would you like? Options: {options}.',
  booking_ask_contact: 'What email address or phone number can we reach you at?',
  booking_invalid_contact: "That doesn't look like an email address or phone number.",
  booking_invalid_service: "Sorry, I didn't catch which service.",
  booking_invalid_text: 'Sorry, I need a short answer for that.',
  booking_submitted:
    'Thanks! Your request has been recorded and is pending confirmation. {business} will contact you at {contact} to confirm. You can close this window now.',
  booking_cancelled: "No problem, I've cancelled this booking request.",
  pleasantry_reply: "You're welcome! Let me know if there's anything else I can help with.",
  nevermind_reply: "No problem. Let me know if there's anything else I can help with.",
  booking_question_unanswered:
    "I can't answer that here, but a team member can help after your request is submitted.",
  cancel_existing:
    "To change or cancel an existing appointment, I've asked a team member to help. They'll reply here.",
  booking_expired: 'Your earlier booking details expired, so we will start again.',
  handoff:
    "I've asked a team member to join. They'll reply here as soon as possible. If you need to leave, please share your email or phone number so we can follow up.",
  unknown_question:
    "I don't have that information yet, so I've passed your question to the team. They'll reply here.",
  message_limit: 'This conversation has reached its message limit. Please contact {business} directly at {contact}.',
} as const;

export type TemplateKey = keyof typeof en;

const zh: Record<TemplateKey, string> = {
  greeting_menu: '您好！我可以为您解答营业时间、服务区域、服务项目与价格，或帮您登记预约。请问有什么可以帮您？',
  answer_hours: '营业时间：{hours}',
  answer_areas: '服务区域：{areas}。',
  answer_services: '我们的服务：{list}',
  answer_pricing: '{list}。{note}',
  booking_ask_field: '请提供您的{label}。',
  booking_ask_service: '您想预约哪项服务？可选：{options}。',
  booking_ask_contact: '请留下可以联系到您的邮箱或电话。',
  booking_invalid_contact: '这看起来不像有效的邮箱或电话号码。',
  booking_invalid_service: '抱歉，我没有识别出您要的服务。',
  booking_invalid_text: '抱歉，这一项请简短回答。',
  booking_submitted: '谢谢！您的预约申请已记录，正在等待确认。{business}会通过 {contact} 与您确认，您现在可以关闭此窗口。',
  booking_cancelled: '好的，已取消本次预约登记。',
  pleasantry_reply: '不客气！如果还有其他问题，随时告诉我。',
  nevermind_reply: '好的，如有其他需要随时告诉我。',
  booking_question_unanswered: '这个问题我暂时无法回答，提交预约后工作人员可以为您解答。',
  cancel_existing: '修改或取消已有预约需要工作人员处理，已为您转接，他们会在这里回复您。',
  booking_expired: '之前填写的预约信息已过期，我们重新开始。',
  handoff: '已为您转接工作人员，他们会尽快在这里回复您。如果您需要离开，请留下邮箱或电话，方便我们跟进。',
  unknown_question: '这个问题我暂时没有相关信息，已转给工作人员，他们会在这里回复您。',
  message_limit: '本次对话已达到消息上限，请直接通过 {contact} 联系{business}。',
};

const TEMPLATES: Record<Locale, Record<TemplateKey, string>> = { en, 'zh-CN': zh };

export function t(locale: Locale, key: TemplateKey, vars: Record<string, string> = {}): string {
  return TEMPLATES[locale][key].replace(/\{(\w+)\}/g, (_match, name: string) => vars[name] ?? '').trim();
}

export function templateKeys(locale: Locale): string[] {
  return Object.keys(TEMPLATES[locale]);
}
