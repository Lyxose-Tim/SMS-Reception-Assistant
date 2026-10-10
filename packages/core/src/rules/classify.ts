export type Topic = 'hours' | 'areas' | 'services' | 'pricing';
type Signal = Topic | 'booking' | 'human' | 'cancel' | 'greeting';

export interface Classification {
  topics: Topic[];
  wantsBooking: boolean;
  wantsHuman: boolean;
  wantsCancel: boolean;
  isGreeting: boolean;
  isQuestion: boolean;
}

const TOPICS: Topic[] = ['hours', 'areas', 'services', 'pricing'];

const EN: Record<Signal, RegExp> = {
  hours: /\b(hours?|open|opening|close|closed|closing)\b/i,
  areas: /\b(areas?|serve|location|located|address|where|neighbou?rhoods?)\b/i,
  services: /\b(services?|offer|provide)\b/i,
  pricing: /\b(price|prices|pricing|cost|costs|how much|rates?|quotes?|fees?)\b/i,
  booking: /\b(book|booking|appointment|schedule|reserve|reservation)\b/i,
  human:
    /\b(human|real person|live agent|agent|staff member|representative|operator|(talk|speak) (to|with) (a |an )?(person|someone|human))\b/i,
  cancel: /\b(cancel|never ?mind|forget it)\b/i,
  greeting: /^\s*(hi|hello|hey|good (morning|afternoon|evening))\b/i,
};

// Chinese has no word boundaries: substring matching only. Keep entries specific
// (e.g. "服务项目", not "服务", which would also match "服务区域").
const ZH: Record<Signal, string[]> = {
  hours: ['营业时间', '几点', '开门', '关门', '营业', '上班时间'],
  areas: ['地址', '在哪', '服务区域', '服务范围', '哪些地区', '哪些区域', '位置'],
  services: ['服务项目', '什么服务', '哪些服务', '提供什么', '业务'],
  pricing: ['价格', '多少钱', '收费', '费用', '报价', '价钱'],
  booking: ['预约', '预定', '预订', '约个时间', '约时间', '上门', '安排时间'],
  human: ['人工', '真人', '客服', '工作人员'],
  cancel: ['取消', '算了', '不约了'],
  greeting: ['你好', '您好', '在吗', '哈喽', '嗨'],
};

const QUESTION_START = /^\s*(what|when|where|how|do|does|are|is|can|could|which|who|will|would)\b/i;
const ZH_QUESTION = ['吗', '什么', '怎么', '多少', '哪', '几', '是否'];

function hit(text: string, signal: Signal): boolean {
  return EN[signal].test(text) || ZH[signal].some((word) => text.includes(word));
}

export function classify(text: string): Classification {
  const isQuestion = /[?？]/.test(text) || QUESTION_START.test(text) || ZH_QUESTION.some((w) => text.includes(w));
  return {
    topics: TOPICS.filter((topic) => hit(text, topic)),
    wantsBooking: hit(text, 'booking'),
    wantsHuman: hit(text, 'human'),
    wantsCancel: hit(text, 'cancel'),
    isGreeting: hit(text, 'greeting'),
    isQuestion,
  };
}
