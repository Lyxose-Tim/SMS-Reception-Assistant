import { createTransport, type Transporter } from 'nodemailer';

export interface MailMessage {
  to: string;
  subject: string;
  text: string;
}

export interface Mailer {
  send(message: MailMessage): Promise<void>;
}

/** smtpUrl like smtps://user:pass@smtp.example.com:465 */
export function createSmtpTransport(smtpUrl: string): Transporter {
  // Bounded waits so a stalled SMTP server cannot hang notification work indefinitely (nodemailer defaults are minutes).
  return createTransport({ url: smtpUrl, connectionTimeout: 10_000, greetingTimeout: 10_000, socketTimeout: 20_000 });
}

export function createSmtpMailer(smtpUrl: string, from: string): Mailer {
  const transport = createSmtpTransport(smtpUrl);
  return {
    async send(message) {
      await transport.sendMail({ from, to: message.to, subject: message.subject, text: message.text });
    },
  };
}

export function createMemoryMailer(): Mailer & { sent: MailMessage[] } {
  const sent: MailMessage[] = [];
  return {
    sent,
    async send(message) {
      sent.push(message);
    },
  };
}
