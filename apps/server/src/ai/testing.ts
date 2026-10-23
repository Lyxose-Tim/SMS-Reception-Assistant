import { MockLanguageModelV4 } from 'ai/test';

const USAGE = {
  inputTokens: { total: 10, noCache: 10, cacheRead: undefined, cacheWrite: undefined },
  outputTokens: { total: 5, text: 5, reasoning: undefined },
};

export interface MockReply {
  text?: string;
  calls?: { toolName: string; input: unknown }[];
  error?: Error;
}

/** AI SDK v7 mock model. `doGenerateCalls` records the prompt each call received. */
export function mockLanguageModel(reply: MockReply): MockLanguageModelV4 {
  return new MockLanguageModelV4({
    doGenerate: async () => {
      if (reply.error) throw reply.error;
      const content = [
        ...(reply.text ? [{ type: 'text' as const, text: reply.text }] : []),
        ...(reply.calls ?? []).map((c, i) => ({
          type: 'tool-call' as const,
          toolCallId: `call-${i}`,
          toolName: c.toolName,
          input: JSON.stringify(c.input),
        })),
      ];
      return {
        content,
        finishReason: { unified: reply.calls?.length ? ('tool-calls' as const) : ('stop' as const), raw: undefined },
        usage: USAGE,
        warnings: [],
      };
    },
  });
}
