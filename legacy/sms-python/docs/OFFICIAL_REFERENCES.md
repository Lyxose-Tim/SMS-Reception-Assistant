# Official references and verification notes

Checked on **October 9, 2026**. These are public official sources, not a legal opinion, registration approval, or certification. Requirements can change; verify again for the client's country, sender type and use case before any live trial. No platform account was opened or changed.

## Twilio

| Official source | Verified fact and project implication |
|---|---|
| [TwiML `<Dial>`](https://www.twilio.com/docs/voice/twiml/dial) | The `action` callback provides `DialCallStatus` for the dial attempt. `completed` means the destination answered and connected. `busy`, `no-answer` and `failed` are distinct unsuccessful outcomes; the demo restricts its candidate follow-up trigger to these. `canceled` is not automatically treated as missed. The ordinary parent call ending is insufficient. |
| [Messaging Policy](https://www.twilio.com/en-us/legal/messaging-policy) | Page says updated April 13, 2026. Consent must match its subject; proof, date and method must be retained. An inbound SMS allows replies in that exchange, not ongoing recurring engagement. A call is not documented here as permission to text. Sender identification and accessible opt-out are required. After withdrawal, one final confirmation is permitted; subsequent messaging requires the recipient's renewed express written consent. The demo's consent records are synthetic examples. |
| [A2P 10DLC](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc) | Application SMS/MMS to the US through a US 10-digit local number requires A2P 10DLC registration, including hobbyist use. Toll-free and short codes use different pathways; exemption from 10DLC does not remove their own requirements. Brand/campaign type and applicable fees must be checked for the actual client. |
| [Webhook security](https://www.twilio.com/docs/usage/security) | Validate `X-Twilio-Signature` with the official SDK helper, the account Auth Token, exact public URL including query string, and all applicable form fields. HTTPS and careful proxy URL configuration matter. The signature is not a freshness timestamp. Do not trust caller-supplied `Host`/forwarded headers or an invented form timestamp as the authority for validation or age. |
| [Advanced Opt-Out](https://www.twilio.com/docs/messaging/tutorials/advanced-opt-out) | A signed `OptOutType` of `STOP`, `START` or `HELP` indicates the provider already handled that keyword and sent its configured response; application code should record it without another confirmation. Provider opt-out filtering and sender-specific behavior remain relevant. START removing a provider block does not establish every permission required by this application. Toll-free behavior differs. |
| [Messages resource](https://www.twilio.com/docs/messaging/api/message-resource) | The authenticated resource has a stable SID, sender/recipient, direction, account, body and `dateCreated`. These support identity/freshness verification. `sent` is carrier acceptance; `delivered` and `undelivered` are separate outcomes. Messages can be split into chargeable segments. A simulated send is no evidence of carrier delivery. |
| [Calls resource](https://www.twilio.com/docs/voice/api/call-resource) | The call resource can be fetched through the authenticated API. Before accepting dial callbacks in a live integration, match parent/child call identity, ownership, route and outcome against trusted provider data or a trusted call ledger. No real call resource was exercised for this demonstration. |

### Engineering conclusions drawn from these sources

These are design judgments, not claims that Twilio mandates this particular implementation:

- Deduplicate inbound SMS by Message SID and dial outcomes by trusted call identity; commit state and outbound intent together before attempting a send.
- A valid signature does not stop replay. Require trusted age/identity checks for first-seen events, keep a durable duplicate ledger, and refuse unresolved events. Handle STOP before any ordinary reply. A delayed START must not erase a newer withdrawal.
- Recheck current opt-out, purpose, operator takeover and all send gates immediately before dispatch. Do not re-send automatically after an ambiguous provider timeout; reconcile first.
- Record delivery callbacks separately, prevent older statuses from regressing terminal outcomes, and do not use callback receipt order as proof of provider chronology.
- A voicemail system may answer and yield `completed`. This first version does not determine whether a human answered. Answering-machine detection, forwarding topology, caller-ID reliability and withheld/non-SMS caller numbers need separate validation.

## Upwork

| Official source | Verified publication information |
|---|---|
| [Enhance your freelancer profile](https://support.upwork.com/hc/en-us/articles/360016144974-How-to-enhance-your-freelancer-profile) | Portfolio fields: title up to 70 characters, role up to 100, description up to 600, up to five skills/deliverables tags. Optional related job links are for actual Upwork work. Do not link this personal demo to a nonexistent contract. Public portfolio material must not expose personal contact details. |
| [Portfolio publishing troubleshooting](https://support.upwork.com/hc/en-us/articles/39295662788115-Why-won-t-my-portfolio-item-publish) | Portfolio images allow JPG/JPEG/GIF/PNG. Recommended 1000×750; minimum 400×300; maximum 4000×4000. The page discusses YouTube/Vimeo video links. This task did not upload a video or verify account-specific fields. |
| [Create a Project Catalog project](https://support.upwork.com/hc/en-us/articles/360057397533-How-to-create-a-project-in-Project-Catalog) | Separate flow with overview/category/search tags, pricing/scope, gallery, requirements, description, steps and FAQ. Up to three tiers; delivery days and revisions are specified. Title should not repeat the platform's “You will get” prefix. This is different from a portfolio item. |
| [Project Catalog gallery requirements](https://support.upwork.com/hc/en-us/articles/1500011309082-How-to-add-images-and-video-to-your-Project-Catalog-project) | Up to 20 JPEG/PNG images, each under 10 MB, 4:3 ratio, neither dimension above 4000 pixels; target 1000×750. One MP4 video, maximum 60 seconds and 100 MB. Avoid contact information, other-company logos/references, text-heavy or poor-quality imagery. An 80-second demonstration needs a shorter Catalog edit. Mask synthetic contact identifiers as well before submission. |
| [Project Catalog review](https://support.upwork.com/hc/en-us/articles/4408644453395-How-we-review-your-Project-Catalog-project) | Description comprises summary, steps and FAQs; personal contact/payment information is excluded and the platform reviews content. Publication and acceptance are not guaranteed by this project. |

## Guru

| Official source | Verification status |
|---|---|
| [Services](https://www.guru.com/help/freelancer/services/) | Official search-indexed excerpts identify Title, Description, Skills, Rate/Hour and Starting at (minimum budget). A service summarizes offered expertise and can connect to relevant work. Full page retrieval returned an error in this research session. |
| [Portfolio](https://www.guru.com/help/freelancer/portfolio/) | Official indexed excerpts describe adding a work sample from Edit Profile → Portfolio, and using text, attachments and embedded media. Full page retrieval returned an error. Numerical field limits, allowed file extensions/sizes, video duration and exact current editor controls were **not verified**. |
| [How Guru works for freelancers](https://www.guru.com/how-it-works-freelancer/) | The official overview separates describing offered services from showcasing portfolios. The drafts follow that distinction. |

Guru drafts below are editable content, not a claim that the editor has Upwork's fields or upload specifications. Confirm the actual Guru editor before manually publishing. No unsupported numeric Guru limits are assumed.
