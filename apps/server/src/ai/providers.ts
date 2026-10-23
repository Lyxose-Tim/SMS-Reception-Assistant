import { createAnthropic } from '@ai-sdk/anthropic';
import { createGoogleGenerativeAI } from '@ai-sdk/google';
import { createOpenAI } from '@ai-sdk/openai';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import type { LanguageModel } from 'ai';
import type { AiProvider } from '../db/schema';

export interface ProviderConfig {
  provider: AiProvider;
  model: string;
  baseUrl: string | null;
}

export type LanguageModelFactory = (config: ProviderConfig, apiKey: string) => LanguageModel;

export const createLanguageModel: LanguageModelFactory = (config, apiKey) => {
  switch (config.provider) {
    case 'openai':
      return createOpenAI({ apiKey, ...(config.baseUrl ? { baseURL: config.baseUrl } : {}) })(config.model);
    case 'anthropic':
      return createAnthropic({ apiKey })(config.model);
    case 'google':
      return createGoogleGenerativeAI({ apiKey })(config.model);
    case 'openai-compatible':
      if (!config.baseUrl) throw new Error('A base URL is required for OpenAI-compatible providers');
      return createOpenAICompatible({ name: 'custom', apiKey, baseURL: config.baseUrl })(config.model);
  }
};
