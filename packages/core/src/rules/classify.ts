export type Topic = 'hours' | 'areas' | 'services' | 'pricing';
type Signal = Topic | 'booking' | 'human' | 'cancel' | 'greeting';

export interface Classification {
  topics: Topic[];
  wantsBooking: boolean;
  wantsHuman: boolean;
  wantsCancel: boolean;
  isGreeting: boolean;
  /** The whole message is a thank-you, acknowledgement or farewell. */
  isPleasantry: boolean;
  isQuestion: boolean;
}

const PLEASANTRY_PHRASES = [
  'thanks', 'thank you', 'thanks a lot', 'thx', 'ty', 'thank', 'thanks again', 'ok', 'okay', 'great', 'cool', 'got it',
  'bye', 'goodbye', 'see you', 'no problem', 'perfect',
  '谢谢', '谢谢你', '谢谢您', '谢谢你们', '谢谢大家', '多谢', '谢啦', '好的', '好', '嗯', '行', '知道了', '再见', '拜拜', '没事', '没事了',
];
const PLEASANTRY_FILLERS = ['so', 'much', 'very', 'again', 'a', 'lot', '你好', '您好', '啦', '了', '呀', '哈'];
const PLEASANTRY_SET = new Set([...PLEASANTRY_PHRASES, ...PLEASANTRY_FILLERS]);
const MAX_PHRASE_WORDS = 3;
const SEPARATORS = /[\s\p{P}\p{S}\p{Extended_Pictographic}\uFE0F\u200D]+/u;
const CJK_RUN = /[^\sa-z0-9']+|[a-z0-9']+/g;
const ZH_UNITS = [...PLEASANTRY_SET].filter((unit) => /[^a-z ]/.test(unit)).sort((a, b) => b.length - a.length);

/** Greedy longest-match split of a Chinese run into known pleasantry units, or null if any part is unknown. */
function segmentZh(run: string): string[] | null {
  const units: string[] = [];
  let i = 0;
  while (i < run.length) {
    const unit = ZH_UNITS.find((u) => run.startsWith(u, i));
    if (!unit) return null;
    units.push(unit);
    i += unit.length;
  }
  return units;
}

const THANKS = /\b(thank|thanks|ty)\b|谢/i;

/** True when the whole message is only pleasantry phrases and fillers (at least one real phrase), however punctuated. */
function isPleasantry(text: string): boolean {
  // A greeting wins over plain acknowledgements; only an explicit thanks makes "你好谢谢" a pleasantry.
  if (hit(text, 'greeting') && !THANKS.test(text)) return false;
  const items: string[] = [];
  for (const chunk of text.toLowerCase().split(SEPARATORS)) {
    for (const part of chunk.match(CJK_RUN) ?? []) {
      if (/^[a-z0-9']+$/.test(part)) {
        items.push(part);
        continue;
      }
      const units = segmentZh(part);
      if (!units) return false;
      items.push(...units);
    }
  }
  let sawPhrase = false;
  let i = 0;
  while (i < items.length) {
    let matched = 0;
    for (let n = Math.min(MAX_PHRASE_WORDS, items.length - i); n >= 1; n--) {
      if (PLEASANTRY_SET.has(items.slice(i, i + n).join(' '))) {
        matched = n;
        break;
      }
    }
    if (!matched) return false;
    if (PLEASANTRY_PHRASES.includes(items.slice(i, i + matched).join(' '))) sawPhrase = true;
    i += matched;
  }
  return sawPhrase;
}

const TOPICS: Topic[] = ['hours', 'areas', 'services', 'pricing'];

const EN: Record<Signal, RegExp> = {
  hours: /\b(hours?|open|opening|close|closed|closing)\b/i,
  areas: /\b(areas?|serve|location|located|address|where|neighbou?rhoods?)\b/i,
  services: /\b(services?|offer|provide)\b/i,
  pricing: /\b(price|prices|pricing|cost|costs|how much|rates?|quotes?|fees?)\b/i,
  booking: /\b(book|booking|appointment|schedule|reserve|reservation)\b/i,
  human:
    /\b(human|real person|live agent|human agent|staff member|representative|operator|(talk|speak) (to|with) (a |an |the )?(person|someone|human|manager))\b/i,
  cancel: /\b(cancel|never ?mind|forget it)\b/i,
  greeting: /^\s*(hi|hello|hey|good (morning|afternoon|evening))\b/i,
};

// Chinese has no word boundaries: substring matching only. Keep entries specific
// (e.g. "服务项目", not "服务", which would also match "服务区域").
const ZH: Record<Signal, string[]> = {
  hours: ['营业时间', '几点', '开门', '关门', '营业', '上班时间'],
  areas: ['地址', '在哪', '服务区域', '服务范围', '哪些地区', '哪些区域', '位置'],
  services: ['服务项目', '什么服务', '哪些服务', '提供什么', '业务'],
  pricing: ['价格', '多少钱', '收费', '费用', '报价', '价钱', '人工费'],
  booking: ['预约', '预定', '预订', '约个时间', '约时间', '安排时间'],
  human: ['转人工', '人工客服', '人工服务', '找人工', '真人', '联系客服', '转客服', '找客服', '找工作人员', '转工作人员', '联系工作人员'],
  cancel: ['取消', '算了', '不约了'],
  greeting: ['你好', '您好', '在吗', '哈喽', '嗨'],
};

const QUESTION_START = /^\s*(what|when|where|how|do|does|are|is|can|could|which|who|will|would)\b/i;
const ZH_QUESTION = ['吗', '什么', '怎么', '多少', '哪', '几', '是否'];

const ZH_QUESTION_END = /[吗呢][\s。！!~～]*$/;

/** Punctuation, an English question opener or a sentence-final 吗/呢; deliberately ignores Chinese question words (e.g. 几) that also appear in plain answers. */
export function isExplicitQuestion(text: string): boolean {
  return /[?？]/.test(text) || QUESTION_START.test(text) || ZH_QUESTION_END.test(text);
}

const RESIDUE_STOP_EN = /\b(the|your|ur|a|an|me|my|for|of|pls|please|info|information|details?|list|menu|sheet)\b/gi;
const RESIDUE_STOP_ZH = ['你们', '请问', '一下', '的', '呢', '啊', '吧', '请', '问', '有'];

/** Text left after deleting the keywords of the given topics (and polite filler), with punctuation and whitespace removed. */
export function topicResidue(text: string, topics: Topic[]): string {
  let rest = text;
  for (const topic of topics) {
    rest = rest.replace(new RegExp(EN[topic].source, 'gi'), ' ');
    for (const word of ZH[topic]) rest = rest.split(word).join(' ');
  }
  rest = rest.replace(RESIDUE_STOP_EN, ' ');
  for (const word of RESIDUE_STOP_ZH) rest = rest.split(word).join(' ');
  return rest.replace(/[\s\p{P}\p{S}]+/gu, '');
}

function hit(text: string, signal: Signal): boolean {
  return EN[signal].test(text) || ZH[signal].some((word) => text.includes(word));
}

// "How many hours does it take?" asks about duration, not opening hours.
function isQuantityQuestion(text: string): boolean {
  return /\bhow (many|long)\b/i.test(text) && !/\b(open|opening|close|closed|closing)\b/i.test(text);
}

export function classify(text: string): Classification {
  const isQuestion = isExplicitQuestion(text) || ZH_QUESTION.some((w) => text.includes(w));
  return {
    topics: TOPICS.filter((topic) => hit(text, topic) && !(topic === 'hours' && isQuantityQuestion(text))),
    wantsBooking: hit(text, 'booking'),
    wantsHuman: hit(text, 'human'),
    wantsCancel: hit(text, 'cancel'),
    isGreeting: hit(text, 'greeting'),
    isPleasantry: isPleasantry(text),
    isQuestion,
  };
}
