export type WidgetLocale = 'en' | 'zh-CN';

const en = {
  openChat: 'Chat with us',
  closeChat: 'Close chat',
  inputPlaceholder: 'Type your message…',
  inputLabel: 'Message',
  send: 'Send',
  typing: 'Typing…',
  talkToPerson: 'Talk to a person',
  talkToPersonMessage: 'I want to talk to a real person',
  humanNotice: 'A team member will reply here as soon as possible.',
  leaveContact: 'Leave your contact details',
  contactIntro: 'Leave your email or phone number and we will get back to you.',
  name: 'Name',
  email: 'Email',
  phone: 'Phone',
  note: 'Message (optional)',
  submitContact: 'Send details',
  cancel: 'Cancel',
  contactThanks: 'Thanks! We will get back to you soon.',
  errorGeneric: 'Something went wrong. Please try again.',
  errorRateLimited: 'You are sending messages too quickly. Please wait a moment.',
  errorTooLong: 'Your message is too long (1000 characters max).',
  errorContact: 'Please enter a valid email address or phone number.',
  errorContactRejected: 'Please check your details and try again.',
  reconnecting: 'Reconnecting…',
} as const;

export type StringKey = keyof typeof en;

const zh: Record<StringKey, string> = {
  openChat: '在线咨询',
  closeChat: '关闭聊天',
  inputPlaceholder: '请输入您的问题…',
  inputLabel: '消息',
  send: '发送',
  typing: '正在输入…',
  talkToPerson: '联系人工客服',
  talkToPersonMessage: '我想找人工客服',
  humanNotice: '工作人员会尽快在这里回复您。',
  leaveContact: '留下联系方式',
  contactIntro: '留下您的邮箱或电话，我们会尽快联系您。',
  name: '姓名',
  email: '邮箱',
  phone: '电话',
  note: '留言（可选）',
  submitContact: '提交',
  cancel: '取消',
  contactThanks: '谢谢！我们会尽快与您联系。',
  errorGeneric: '出了点问题，请稍后再试。',
  errorRateLimited: '消息发送太频繁，请稍候再试。',
  errorTooLong: '消息太长了（最多 1000 个字符）。',
  errorContact: '请填写有效的邮箱或电话号码。',
  errorContactRejected: '请检查您填写的信息后重试。',
  reconnecting: '正在重新连接…',
};

export const STRINGS: Record<WidgetLocale, Record<StringKey, string>> = { en, 'zh-CN': zh };

function toLocale(value: string | null | undefined): WidgetLocale | null {
  const v = value?.toLowerCase() ?? '';
  if (v.startsWith('zh')) return 'zh-CN';
  if (v.startsWith('en')) return 'en';
  return null;
}

/** data-locale override → visitor's browser language → business default. */
export function pickLocale(override: string | null, browserLanguage: string | undefined, businessLocale: WidgetLocale): WidgetLocale {
  return toLocale(override) ?? toLocale(browserLanguage) ?? businessLocale;
}

export function tr(locale: WidgetLocale, key: StringKey): string {
  return STRINGS[locale][key];
}
