# @reception/widget

The chat bubble merchants add to their website. It is one file, `widget.js` (about 14 KB gzip), with all styles isolated in a Shadow DOM so it cannot clash with the site's CSS.

## Embed on a website
1. Via the admin API (dashboard arrives in a later release), add the website's origin, e.g. `https://www.example.com`, to the business's **allowed origins**. `www.example.com` and `example.com` are different origins.
2. Paste before `</body>`:
   ```html
   <script src="https://chat.your-domain.com/widget.js" data-business="your-slug" async></script>
   ```
Optional attributes: `data-locale="zh-CN"` (force the language; default: visitor's browser language), `data-api="https://…"` (API server, default: where `widget.js` is served from).

## Content Security Policy
If the merchant site sends a `Content-Security-Policy`, it must allow the chat server's origin (e.g. `https://chat.your-domain.com`):
- `script-src https://chat.your-domain.com` (loads `widget.js`)
- `connect-src https://chat.your-domain.com` (API calls and the live message stream)
- `style-src 'unsafe-inline'` (the widget injects a `<style>` element into its Shadow DOM)

## Behaviour
- The conversation is created when the visitor sends their first message; it survives page reloads (stored in `localStorage`, or memory in private mode).
- Replies from a team member arrive live while the panel is open.
- When a team member takes over, the visitor can leave an email or phone number.

## Develop
- `npm run build:widget` (repo root) builds `apps/widget/dist/widget.js` and fails if it exceeds 40 KB gzip.
- The server serves it at `/widget.js` and a fictional demo page at `/demo?business=<slug>`.
