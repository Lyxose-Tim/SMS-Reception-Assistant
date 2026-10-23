import { describe, expect, it } from 'vitest';
import { createSmtpTransport } from './mailer';

describe('createSmtpTransport', () => {
  it('bounds connect, greeting and socket waits while keeping the URL settings', () => {
    const transport = createSmtpTransport('smtps://user:pass@smtp.example.com:465');
    const options = (transport as unknown as { transporter: { options: Record<string, unknown> } }).transporter.options;
    expect(options).toMatchObject({
      host: 'smtp.example.com',
      port: 465,
      secure: true,
      connectionTimeout: 10_000,
      greetingTimeout: 10_000,
      socketTimeout: 20_000,
    });
    transport.close();
  });
});
