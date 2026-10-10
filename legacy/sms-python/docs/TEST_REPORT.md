# 当前独立最终验收 — 2026-10-09 本地日期 / 2026-10-10 UTC

**当前唯一选定 full-final-02：382 项收集、380 项执行；377 完整通过、3 部分、
2 整项受限；0 失败、0 错误、0 未捕获线程异常。** 原 315 ID 全部保留，新增 67、
移除 0；原 72 skip ID 为 69 完整通过、3 部分，原 45 SDK 全部实际通过。
`unittest skipped=6` 分为 2 整项及 4 个符链权限子场景，部分项未冒充完整通过。

本轮正式 PowerShell Start → 项目 `.venv` → `app.py` / `APP_MODE=service` 的新浏览器
核心 41、入口 4、恢复 7 已重新执行；同轮补充 FAQ/禁用/期限/重复点击/导航/空库/会话
过期 43，合计 95，不计历史 UI 41＋4＋7。实际桌面物理双击、复用、UI 正常退出、重启
及最终冻结版本启动通过；临时服务和合成数据清理见下列详细报告，快捷方式保留。

详见 [FINAL_ACCEPTANCE_REPORT](FINAL_ACCEPTANCE_REPORT.md)、
[Windows 快速开始](WINDOWS_QUICK_START.md)、[当前 72-ID 矩阵](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/original-72-current-closure.md)、
[UI 明细](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-acceptance-summary.json) 和 [平台条件](PLATFORM_SUPPORT.md)。
在线备份允许业务并行，真实 SH/EX、并发写、SQLite 一致性、超时失败/重试均验证；
恢复及破坏性维护仍停服排他。客户私有目录/跨用户访问、Linux、本机真实控制台信号、
真实 SMS E2E 仍未验收，不声称 production-ready。

---

## 历史续篇 — 下方 315 及 283 正文完整保留

**下方此前“当前/Current”仅属于各自历史运行，不是本次最终文件验收结果。**
本次修改前文件作为准确字节后缀保留，原日志/JSON/截图及外部基线不覆盖。

# 当前 Windows 原生对齐结果 — 最终入口版本

**本轮当前结果：315项收集，313项实际执行；310完整通过、3部分执行、2整项受阻；
0失败、0错误、0未捕获线程异常。** 选定 `full-windows-03`，入口源码修复后运行，
不是旧283/211/72或Linux历史结果。原72-ID保留：69通过、3部分；45 SDK原项全部通过。

实际PowerShell Start → 项目`.venv` → `app.py` / `APP_MODE=service`；最终登录后核心
UI41断言重新通过，入口4断言及恢复7断言分开记录，不累计重复运行。
demo/service持久库共用真实跨进程锁；备份、恢复、维护及DACL前后证据已记录。

**Windows客户数据运行仍候选**：私有目录/跨用户token、符链权限、8.3别名及指定网络
reparse真实测试条件不足；本轮未测试Linux、未执行真实SMS E2E，不声称production-ready。

详细续篇：[WINDOWS_TEST_REPORT.md](WINDOWS_TEST_REPORT.md)；
[72-ID逐项矩阵](WINDOWS_SKIP_MATRIX.md)；[平台判定](PLATFORM_SUPPORT.md)。
原生Plan、批准、命令/时间/退出码、环境、日志、截图、清理、前后哈希与最终源码diff
均由详细续篇索引。UTC与本地时间分开；原JSON的`+00:00`没有错误、没有修改。

---

## 历史正文 — 原283/211/72优化报告完整保留

**下面原文中的“Current”属于前一轮优化时点，仅为历史归属，不是本轮当前状态。**
以下正文逐字节保留，原始文件另存于本轮before-source及外部baseline-source。

# Current implementation verification — 2026-10-09 (client date)

This report belongs to the approved optimization implementation. It records new
Windows offline and authenticated browser checks; it does not claim complete
Linux/SDK acceptance, real SMS delivery or production readiness.

## Evidence provenance

| Run | Result | Attribution |
|---|---|---|
| Historical independent Windows run | 218: 197 passed, 1 failure, 20 errors, 0 skipped | User-provided earlier independent run; not rerun statistics |
| Original Linux report | 218 passed in its recorded Linux environment | [Preserved original report](TEST_REPORT_ORIGINAL_LINUX.md); not this implementation run |
| Current Windows offline run | 283: **211 passed, 0 failures, 0 errors, 72 skipped** | [Current log](../artifacts/offline-regression-current.txt), [structured results](../artifacts/offline-regression-current.json) |
| Current authenticated UI | **41 assertions passed**, 4 phases | [UI summary](../output/playwright/ui-acceptance-summary.json), phase logs and screenshots |
| Complete supported Linux + SDK regression | Pending | No approved existing Linux environment was provided |
| Real E2E | Not executed | Separate sender/registration/participant/content/segments/cost/permission approval required |

Current runtime: Windows-11-10.0.26200-SP0; Python 3.13.9 | packaged by Anaconda, Inc. | (main, Oct 21 2025, 19:09:58) [MSC v.1929 64 bit (AMD64)];
SQLite 3.51.0. Twilio SDK is absent in the available Python; the declared
version remains 9.11.2. Skip reasons identify **45 SDK-dependent** and
**27 Linux-only** cases. They are pending checks, not business passes.
Exact execution timestamps are in the structured evidence.

The initial sandbox default temporary directory could not be opened by SQLite;
the final runner sets temporary files inside this workspace for its own process.
That environment error is not a business-regression result. No dependency,
WSL, Docker, system setting, real credential or deployment was installed/configured.

## Implemented behavior exercised locally

- Atomic STOP acceptance and withdrawal; deterministic STOP-before-submit and
  already-in-flight ordering, current retry claims, pending legacy STOP, early
  callbacks without database/network deadlock, uncertain outcomes without resend.
- Durable provider-source authority, human-only reviewed unknown inquiries,
  original resource deadline, fixed outbox snapshots, missing/deleted sources,
  START proof/restoration barriers and no marketing enrollment.
- Explicit test/formal recipient policies, independent formal approval, service
  purpose restriction, pause/emergency/restore/segment/minute/day/contact-hour gates.
- Fifth requests FAQ, independent request drafts, complete/multi-turn requests,
  conservative old-data upgrade, event-ID deduplication and pending confirmation.
- Trusted single-hop login buckets, spoofed/malformed headers, malformed Cookies,
  strict password hashes, authentication/CSRF and bounded shutdown.
- Portable restore-freeze check: even a valid SQLite file lacking settings gets
  pause and independent review gates before Engine reconstructs schema.

Actual process exclusion, POSIX permissions/symlinks, consistent backup/restore,
retention/deletion integration and applicable official-SDK cases remain pending.
Windows skips cannot establish those Linux behaviors.

## Authenticated browser acceptance

Browser: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/154.0.0.0 Safari/537.36; timezone America/Los_Angeles.
Existing cached Playwright and installed Chrome were used, with a separate profile
and a loopback-only synthetic fixture. Real sending remained false; a fake provider
would reject any send. **Provider submission attempts: 0.**

The 41 assertions cover login gating; explicit unchecked
unknown-inquiry confirmation; real human queue creation; fixed human-only source
and deadline; takeover/resume; STOP blocking/cancellation; five approved editors
and the edited fifth answer; second-request re-collection and two pending requests;
queue cancellation/pause; audit and consent UI; privacy masking; CSRF; logout and
readable invalid-login feedback. Final browser JavaScript-runtime error list is empty.
Expected unauthenticated/invalid-CSRF responses and an unserved favicon are recorded
separately from application failures. Early acceptance-script selector/click-target
diagnostics were corrected and repeated; the four final phase logs contain success.

Selected evidence:

- [Human-only reply queued](../output/playwright/02-human-reply-queued.png)
- [Second request starts with new fields](../output/playwright/06-second-request-new-draft.png)
- [Two independent pending requests](../output/playwright/07-two-pending-requests.png)
- [Final STOP and privacy view](../output/playwright/10-final-reviewed-stop.png)
- [Login/exit feedback](../output/playwright/09-logout-and-login-error.png)

The fixture server and browser are stopped. Synthetic database/profile/temp cleanup
is recorded in the UI summary; screenshots, structured summaries and logs remain.
This fixture adds no simulation route or permission bypass to the application.

## Reproduce and remaining acceptance

Run `python scripts/run_offline_regression.py` with existing project dependencies.
On an approved existing Linux host with Twilio 9.11.2, execute the complete suite
and inspect every failure/error/skip. The Linux process/operations scenarios are
retained as real tests; do not substitute mocked locks or remove gates.

UI fixture and check scripts are in `scripts/ui_acceptance_*.py/js`; use a separate
profile, the documented synthetic password and local fake inbound commands. They
must not be launched with client data or mistaken for a production service.

Current offer: $350 / 10 days / 1 agreed-scope revision, one qualified client-owned
number, up to five FAQs and the specified reception features/source/authorized real
testing. Third-party costs belong to the client; see [scope review](SERVICE_SCOPE_REVIEW.md).
Controlled real E2E remains a delivery prerequisite, with its concrete approval
manifest in [REAL_E2E_PLAN](REAL_E2E_PLAN.md). No real test, deployment or publication
is claimed, and the engineering is not marked production-ready.
