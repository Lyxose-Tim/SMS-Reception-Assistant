import type { ModelAction, ReplyModel } from '@reception/core';
import { generateText, tool, type LanguageModel } from 'ai';
import { z } from 'zod';

/**
 * Tools have no `execute`: the calls are returned to the core engine, which validates them
 * and produces every commitment (booking submitted, handoff) from fixed templates.
 */
export const REPLY_TOOLS = {
  update_booking_draft: tool({
    description: 'Save booking details the visitor has provided. Use the booking field keys listed in the instructions as keys.',
    inputSchema: z.object({ fields: z.record(z.string(), z.string().max(200)) }),
  }),
  submit_booking_request: tool({
    description: 'Submit the booking request after every required booking field has been saved.',
    inputSchema: z.object({}),
  }),
  handoff_to_human: tool({
    description: 'Ask a team member to take over when the business information does not answer the question or the visitor asks for a person.',
    inputSchema: z.object({ reason: z.string().max(200) }),
  }),
};

export function createAiReplyModel(model: LanguageModel): ReplyModel {
  return {
    async generate(input) {
      const result = await generateText({
        model,
        instructions: input.system,
        messages: input.messages,
        tools: REPLY_TOOLS,
        abortSignal: input.signal,
        maxOutputTokens: input.maxOutputTokens,
        maxRetries: 0,
      });
      const actions: ModelAction[] = [];
      for (const call of result.toolCalls) {
        if (call.dynamic || (call as { invalid?: boolean }).invalid) continue;
        if (call.toolName === 'update_booking_draft') actions.push({ type: 'update_booking_draft', fields: call.input.fields });
        else if (call.toolName === 'submit_booking_request') actions.push({ type: 'submit_booking_request' });
        else if (call.toolName === 'handoff_to_human') actions.push({ type: 'handoff_to_human', reason: call.input.reason });
      }
      return { text: result.text, actions };
    },
  };
}
