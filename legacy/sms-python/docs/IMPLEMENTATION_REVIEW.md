# 当前独立交付验收复核 — 最终冻结版本

新独立完整运行 full-final-02：382 收集、380 执行、377 完整通过、3 部分、2 整项
受限、0 失败/错误/线程异常；56 个运行源码前后相同。保留全部历史 315 ID，新增
67 个，原 72-ID 为 69 完整/3 部分。新正式服务 GUI 41＋4＋7、额外 43，本轮各阶段
及实际桌面鼠标双击的责任归属见 [最终报告](FINAL_ACCEPTANCE_REPORT.md)。

本轮独立复现并修复：预约同句旧/新字段歧义保守转人工且不改草稿/原授权期限；
桌面输出路径在副作用前验证/预留，启动失败按已记录身份清理；demo 受管理健康/退出
与 HTTP drain，service 登录门禁不降级；品牌/返回/前进/刷新路由同步；原生 manifest
发布对短暂禁止 delete sharing 有界重试。桌面独立合成数据库不打开客户库，实际
`.venv` 单实例后台仅是用户本轮授权的本地演示，不创建系统服务或计划任务。

在线备份按用户明确边界：公共 backup 持原生 maintenance SH，服务业务继续；
恢复/清理等按 service→maintenance EX。实际并发 writer/WAL/一致性校验、默认 5 秒
及自选预算超时/no-clobber、恢复冲突拒绝和重试通过，源不是文件复制。
同步系统调用不是硬实时可中断保证，任意绕过协议的外部 SQLite 工具未获支持。

仍受限：4 符链子场景实际 1314；D 盘原短名测试无现存别名，但 C 盘现存 8.3 父路径
另有真实子进程拒绝/释放证明；378 个现有进程 token 只读扫描没有可用独立用户身份，
不代表全系统没有其他用户。DACL/owner/group 原字节保留，完整 SD 继承标志可能改变；
真实错误 32、真实文件移动构造的 missing-name recovery、未复现 1177 分开记录。
客户数据私有目录及跨用户拒绝访问仍缺证据；不改 ACL/系统设置。Linux、真实控制台
Ctrl+C/Ctrl+Break、网络重解析 fixture 和真实短信仍未验收。

当前代码、文档完成后重新生成 [源码差异](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/source-changes.diff)、
[实施后清单](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/after-manifest.json)；没有 `.git`，未冒充 git diff。
商业范围与真实 E2E 单独授权边界继续保持，详见最终报告。

---

## 历史实施复核 — 下方原文完整保留

以下“当前/本轮”属于之前原生对齐阶段，历史源码和运行证据不被本轮新结果替换。

# 当前 Windows 原生实施复核 — 2026-10-09本地日期

已按原生Plan审阅和实施批准完成专项内增量修复。最终版本选定full-windows-03：
315收集、313执行、310完整通过、3部分、2整项受阻、0失败/错误/后台线程异常。
原72-ID完整保留；45 SDK原测试实际通过；最终正式服务登录后UI41、入口4、恢复7
分别验证。详细[当前结果](WINDOWS_TEST_REPORT.md)、[矩阵](WINDOWS_SKIP_MATRIX.md)
及[平台判定](PLATFORM_SUPPORT.md)有实际命令、时间、进程和原生文件操作证据。

所有持久化app入口（demo/service）在Engine前共用LockFileEx/flock及恢复marker检查；
活动service库被demo写入的缺口已用真实子进程拒绝及DB/表哈希不变证明关闭。
`:memory:`仅保留demo；service必须持久库。PowerShell Start实际运行项目venv的app.py，
Stop按run_id、实际PID及OS创建身份触发自身drain，未按端口/名称强杀进程。
worker.start失败和端口初始化失败之后的锁、数据库与端口重取实际通过。

Windows SQLite备份/原生无覆盖发布/停写恢复/独立rollback/marker恢复已验证；
正常restore真实DACL/owner/group字节一致，但SD加入0x0400继承标志。missing-name状态
由真实移动构造后回滚，原始SD/DACL一致；真实sharing失败32观察到，1177未复现。
未使用AccessCheck冒充原DACL保留或跨用户私密证明。路径在目标IO前做语法、固定盘、
NTFS及中间reparse目标检查，真实本地junction通过；不能验证的边界fail closed。

剩余真实阻塞：当前客户数据ACL宽，缺私有目录及获准跨用户token；4原符链子场景
缺权限，8.3没有现存别名，指定网络/UNC/未知reparse没有真实fixture。本轮不改ACL、
用户/系统设置或创建网络共享。Windows只对本报告合成本地配置验收，客户运行仍候选。
Linux仅保留选项未重跑；真实E2E仍需账号/号码注册资格、参与者、精确内容/条数/分段、
回调、预算/时间与停止清理授权，未配置真实凭据、发送、部署或发布。

当前商业范围仍$350/10天/1修改、单商家/单合格客户Twilio号码、最多5批准FAQ、
预约需人工确认、接管人工回复/退订日志、源码及授权真实测试，第三方费客户承担。
下方已有业务修复说明及历史证据归属继续保留；最终diff/清单在本文及全部文档完成后生成。

---

# Optimization implementation review — evidence kept separate

This change extends the existing Python + SQLite project within the approved
scope; it does not replace the project. Current Upwork terms are **$350 / 10 days /
1 agreed-scope revision**, one business, one qualified client-owned Twilio number,
at most five approved FAQs, pending owner-confirmation requests, takeover/human
replies, opt-out/audit views, source and authorized controlled real SMS testing.
Third-party costs belong to the client. Real testing remains unexecuted.

## Changed behavior and acceptance scenarios

- STOP intake, withdrawal/queue cancellation and final outbox submission share a
  local ordering boundary. Exercise STOP-before-send and send-before-STOP with
  deterministic thread barriers; include existing duplicate/restart behavior.
  A new STOP is successfully accepted only after atomic withdrawal commits;
  subsequent business submissions are blocked. Request arrival and waiting-handler
  priority are not the boundary. Bounded wait timeout returns a retryable failure,
  without acknowledging intake, so webhook retries remain necessary. An in-flight
  send may finish before commit, and provider-accepted SMS cannot be recalled.
  Linux single-instance lock excludes a second process; shared-DB multi-worker
  deployment is unsupported.
- Verified unknown inquiries may be reviewed by the operator. Audit the source,
  original deadline and human-only service basis. A new `Are you insured?` handoff
  permits a confirmed relevant team reply; no automatic/marketing scope is created.
  Test expiry, wrong/unverified/deleted source, STOP/carrier blocks, idempotent
  repeated review and takeover behavior. Review does not reset the original window.
- The approved requests FAQ answers appointment-rule questions. Real appointment
  submissions continue collecting fields; a new request never silently reuses old
  location/time/contact details. Test all five FAQs, complete/multi-turn requests,
  a second request and pending-owner-confirmation output.
- Default test-allowlist policy stays restrictive. Explicit inbound-service policy
  handles fresh verified new-customer service replies while retaining every other
  gate. Test formal/test policies, unverified/arbitrary proactive attempts,
  STOP/carrier/contact blocks, pause/emergency, limits and restore review.
- Login rate limiting uses only trusted proxy identity; invalid/multi-valued or
  spoofed forwarded data cannot evade limits. Exercise malformed Cookies and
  bounded, validated password-hash parameters without server errors or excessive
  work; preserve authentication/CSRF/session rules.
- The operator UI shows recipient policy read-only and the reviewed inquiry body,
  receipt time and deadline. The unknown-inquiry confirmation is explicit and
  unchecked by default. Test login, reply, takeover/resume, requests, consent,
  profile, delivery queue, audit, privacy toggle, logout and blocked-action feedback.

## Platform and run provenance

The Windows alignment now provides an isolated project venv and PowerShell entry
for the real `app.py` service process, native ownership, normal stop, offline tests
and synthetic operator commands. The final positive startup and signed-in UI used
this entry; fixture results do not establish it. Windows customer-data acceptance
remains candidate/blocked on private storage/ACL evidence. Native interprocess
tests and operations have selected Windows evidence with partial/capability results recorded separately. Linux/POSIX assertions
are retained but are not silently counted as newly executed Windows evidence.
No WSL/Docker, background service, ACL, PATH or execution-policy change is installed.

The historical independent Windows result was **218 discovered / 197 passed /
1 failed / 20 errored / 0 skipped**. The original Linux report recorded **218
passed** on its own environment. These remain separate historical evidence;
these numbers are historical; the current alignment counts are in the dated section above. The preceding optimization report and its
283/211/72 run and 41 UI assertions remain historical for this alignment. New run
environments, counts, original-ID skip closure, logs and real-entry UI evidence
belong to [WINDOWS_TEST_REPORT.md](WINDOWS_TEST_REPORT.md) and
`artifacts/windows-alignment-runs/`.
Do not count Linux-only Windows skips as business passes or mock status as delivery.

`scripts/windows.ps1` writes new run metadata only. It uses the project interpreter,
clears inherited live/provider configuration, preserves actual OS app/launcher
identities, and requests app-owned normal shutdown without killing unrelated PIDs.
The synthetic operator actions still require offline/review confirmation and
the existing real lock, verification, restore-pause and safety-state gates.

## Boundaries and risks

STOP ordering may wait for a provider submission already in progress, bounded by
the request timeout. A wait timeout does not constitute successful withdrawal
acceptance; no priority or raw-arrival guarantee is made. New permission fields/migrations must fail closed for old or
incomplete sources; old consent is not a license for arbitrary outreach. Review
uses the operator's judgment about a service question and records that judgment,
not legal marketing enrollment. The fixed English FAQ parser remains narrow.

Real E2E needs its exact separately authorized manifest after offline/UI acceptance;
see `REAL_E2E_PLAN.md`. No real send, account/secret setup, purchase, public exposure,
deployment or publication is performed by local implementation. No production-ready,
LLM, voice, CRM, calendar or multi-tenant claim is added. Ongoing encrypted backups,
retention scheduling, old-copy/provider cleanup, incidents and maintenance remain
with the client/named operator under agreed terms; live-test cleanup includes
turning off sending, queue review and temporary access/exposure revocation.
