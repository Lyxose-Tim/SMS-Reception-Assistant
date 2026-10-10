import { describe, expect, it } from 'vitest';
import { classify } from './classify';

describe('classify topics', () => {
  it.each([
    ['What are your hours?', ['hours']],
    ['Are you open on Saturday?', ['hours']],
    ['你们几点开门？', ['hours']],
    ['Do you serve Riverside?', ['areas']],
    ['服务区域有哪些', ['areas']],
    ['How much is a deep clean?', ['pricing']],
    ['深度清洁多少钱', ['pricing']],
    ['What services do you offer?', ['services']],
    ['你们提供哪些服务', ['services']],
  ])('%s → %j', (text, topics) => {
    expect(classify(text).topics).toEqual(topics);
  });
});

describe('classify intents', () => {
  it('detects booking in English and Chinese', () => {
    expect(classify('I want to book an appointment').wantsBooking).toBe(true);
    expect(classify('我想预约上门打扫').wantsBooking).toBe(true);
  });

  it('detects a request for a human', () => {
    expect(classify('Can I talk to a real person?').wantsHuman).toBe(true);
    expect(classify('转人工').wantsHuman).toBe(true);
  });

  it('does not treat "someone" in an ordinary question as a human request', () => {
    expect(classify('Can someone clean on Sunday?').wantsHuman).toBe(false);
  });

  it('does not treat "cancellation policy" as a cancel command', () => {
    expect(classify('What is your cancellation policy?').wantsCancel).toBe(false);
    expect(classify('cancel').wantsCancel).toBe(true);
    expect(classify('算了，取消吧').wantsCancel).toBe(true);
  });

  it('detects greetings and questions', () => {
    expect(classify('hello').isGreeting).toBe(true);
    expect(classify('您好').isGreeting).toBe(true);
    expect(classify('Do you bring supplies').isQuestion).toBe(true);
    expect(classify('可以带宠物吗').isQuestion).toBe(true);
    expect(classify('Alex').isQuestion).toBe(false);
  });
});
