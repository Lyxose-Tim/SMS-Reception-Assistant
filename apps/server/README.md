# @reception/server

Single-process API server for the reception assistant: widget API, admin API, live updates (SSE), email notifications and the AI adapter (bring your own key). Data lives in one SQLite file.

## Run locally
1. `npm install` (repo root)
2. Copy `.env.example` to `.env` at the repo root (the npm scripts load it automatically) and set `APP_SECRET`. Generate one with `node -e "console.log(require('crypto').randomBytes(32).toString('base64url'))"`. It must be ≥ 32 random characters and must stay stable — it encrypts merchants' AI keys; the placeholder value is rejected. Blank values (e.g. `SMTP_URL=`) are treated as unset.
   Without a file, set variables in the shell instead. Windows PowerShell: `$env:APP_SECRET="<generated value>"` (applies to that session only); bash: `export APP_SECRET=...`.
3. Seed a fictional demo business: `npm run cli -w @reception/server -- seed-demo --email you@example.com --password "a long password"`
   Warning: a `--password` given on the command line is saved in your shell history. Use a throwaway first password and change it later (a future UI will offer this), or clear the history afterwards.
4. `npm run dev:server` → http://127.0.0.1:8787/api/health

Create a real business owner instead of the demo: `npm run cli -w @reception/server -- create-owner --slug acme --name "Acme" --email owner@acme.com --password "…"`

## API overview
- Widget (public, CORS per business `widget.allowedOrigins`): `/api/widget/b/:slug/config`, `POST /api/widget/b/:slug/conversations`, `/api/widget/c/:id/messages` (GET/POST, `Authorization: Bearer <visitorToken>`), `POST /api/widget/c/:id/contact`, `GET /api/widget/c/:id/events` (SSE).
- Auth: `POST /api/auth/login|logout`, `GET /api/auth/me` (HttpOnly cookie; state-changing calls must send `Origin: PUBLIC_ORIGIN`).
- Admin: `/api/admin/conversations…`, `/business`, `/faqs`, `/bookings…`, `/ai`, `/events` (SSE).

## Behaviour notes
- The AI only answers from the business profile/FAQs; without a key, or when the provider fails or takes > 15 s, the keyword rules answer instead.
- Bookings are always created as `pending`; only the owner confirms or declines.
- "Talk to a person" or the 100-message limit hands the chat to a human (the bot stops). Unknown questions flag the chat and email the owner, but the bot keeps answering.
- Emails are sent only when `SMTP_URL` and `MAIL_FROM` are set.
- `APP_SECRET` must stay stable. Changing it makes stored AI keys undecryptable, and chats fall back to the keyword rules until the merchant re-enters the key.

## Deploying behind a reverse proxy
- Set `PUBLIC_ORIGIN` to the public https origin (e.g. `https://chat.example.com`). It is the only origin allowed for state-changing admin/auth requests, an always-allowed widget origin, the base for links in notification emails, and (when https) turns on the `Secure` session cookie.
- Set `TRUST_PROXY=true` only when the app is reachable exclusively through the proxy **and** the proxy appends the client address to `X-Forwarded-For` (nginx: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`). Otherwise clients can spoof their IP and dodge the per-IP rate limits.
- Disable proxy buffering for the SSE endpoints `/api/widget/c/*/events` and `/api/admin/events` (nginx: `proxy_buffering off;`). The server already sends `X-Accel-Buffering: no`, which nginx honours unless buffering is forced on.
- Keep the app bound to `127.0.0.1` (the default `HOST`) and let the proxy terminate TLS.

## Prompt-injection check against a real model (costs money)
`EVAL_PROVIDER=openai EVAL_MODEL=<model> EVAL_API_KEY=<key> npm run eval:prompts -w @reception/server`
