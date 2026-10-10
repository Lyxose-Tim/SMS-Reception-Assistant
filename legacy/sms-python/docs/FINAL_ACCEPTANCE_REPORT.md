# 本轮最终验收报告 — Windows 本地演示与正式服务条件

最新冻结的备份/导航版本已完成独立完整离线验收：**382 项收集、380 项实际执行，
377 项完整通过、3 项部分执行、2 项整项受限；0 失败、0 错误、0 未捕获线程异常**。
唯一选定 full-final-02，不累计此前 369 或历史 315；原 72 个跳过 ID 与 45 SDK 原项
均关联本次真实执行。**新正式服务 UI 核心 41＋入口 4＋恢复 7，另有同轮补充 43，
合计 95 项；真实桌面双击、复用、退出、重启与清理均已记录。**

独立验收 agent 负责业务复现、新补充测试、完整离线执行及证据核对；
Root 负责正式服务登录后浏览器验收、真实桌面快捷方式双击及最终文档汇总。
**独立离线签注完成；Root 的正式服务 GUI、桌面与清理证据分别归档，不冒充独立 reviewer 亲自操作。**
真实 SMS E2E 未执行，Windows 客户数据运行仍为候选/受阻，不声称生产就绪。

## 1. Windows 本地演示能否稳定使用？

**在本次验收配置下可以使用本地合成演示。** 已有真实 Windows 11、本地固定 NTFS、
现有项目 `.venv`、回环 HTTP、真实进程互斥、重复启动复用、正常退出及重启证据。
桌面演示固定为 `DEMO / SIMULATED`，真实发送关闭；不需要 WSL、Docker 或 Linux
来执行本机演示。日常演示入口及正常退出方法见 [Windows 快速开始](WINDOWS_QUICK_START.md)。

这项结论覆盖本次配置和执行过的流程，不等同于长期负载、持续运营或客户数据私密性验收。
本轮没有安装后台服务、修改系统执行策略/PATH/ACL、开启 Developer Mode 或改变系统账户。

## 2. Windows 正式服务还缺哪些证据或条件？

**正式服务进程、离线业务与原生运维已有本机合成证据；客户数据运行仍未获验收。**
当前完整运行的真实锁、在线备份/恢复、恢复复核、退出排空和发送门禁通过，不足以证明别的 Windows 用户
无法访问客户记录。仍需获准的私有客户目录、适用访问控制、独立用户 token 和真实拒绝访问
验证。当前 token 可访问、DACL 保留以及文件身份相同，都不能替代跨用户私密性证明。
路径检查也依赖受控目录，不能替代抵抗其他账户并发改目录所需的权限条件。

真实运营还需完成客户自有 Twilio 账户/号码资格、注册与实际路由、商家批准信息、
安全操作员认证及稳定隐私 HMAC key、经批准的 HTTPS intake/status callback 环境、
收件人策略与相应批准、受控真实测试以及明确运维责任。具体对外操作需后续单独授权，
本轮没有配置真实凭据或开放公网。备份加密、保留期调度、旧备份/provider 数据清理、
费用监控、事故处置和持续维护仍须由客户/指定维护人承担或另定协议。

## 3. 原有问题和此前跳过项分别如何关闭？

### 最新冻结源码的一次完整离线执行

此节仅选定备份/导航改动后的新完整运行；不累计两次统计。
最终离线运行：
[full-final-02/regression.json](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/regression/regression.json)。
实际链路为项目 `.venv` 的 `scripts/run_final_acceptance.py` → 现有 PowerShell
`scripts/windows.ps1 -Action Test` → 项目 `.venv` 的 `scripts/run_offline_regression.py`
（完整 `test*.py`，没有目标 ID 过滤）。

| 项目 | 本轮实际记录 |
|---|---|
| 平台 | Windows 11 build 26200，64 位 |
| 解释器 / SQLite / SDK | 项目 `.venv` Python 3.13.9 / SQLite 3.51.0 / Twilio 9.11.2 |
| 外层命令开始、结束 UTC | 2026-10-10T05:55:31.029259+00:00 → 05:57:51.565346+00:00 |
| runner 开始、结束 UTC | 2026-10-10T05:55:31.850539+00:00 → 05:57:51.367038+00:00 |
| runner 时长 / 进程退出码 | 139.516512 秒 / 0 |
| 收集 / 选择 / 实际执行 | 382 / 382 / 380 |
| 完整通过 / 部分执行 / 整项受限 | 377 / 3 / 2 |
| 失败 / 错误 / 未捕获线程异常 | 0 / 0 / 0 |

`unittest skipped=6` 是 **2 个整项 + 4 个权限子场景**，不是 6 个整项。
3 个 partial 不计作完整通过。382 = 377 + 3 + 2；实际执行 380 = 377 + 3。
此前 315 个 ID 全部仍在，新增 67 个、移除 0 个：17 个独立业务、20 个独立安全/运维、
14 个桌面进程、3 个 demo/manifest 与 13 个在线备份并发方法。本次新增 67 个方法在同一完整运行中均通过，
不累计此前定向重复运行。

[命令请求](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/execution-request.json)、
[命令结果](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/execution-result.json)、
[完整日志](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/regression/regression.log)、
[stdout](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/launcher.stdout.log) 和
[stderr](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/launcher.stderr.log)
保留实际 argv、cwd、时间与退出码；最终外层 stderr 为空。

运行前后 56 个明确列入的公开源码/测试/脚本/迁移/网页/依赖及已知合成业务配置文件
SHA256 完全相同。独立 reviewer 随后逐项核对 382 份 case JSON 与 aggregate 相等，
再次检查这 56 个文件仍匹配 after manifest。没有读取或散列未知数据库、实际 `.env`
或客户内容，也不声称 Git diff。见
[before manifest](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/source-before.json)、
[after manifest](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/source-after.json)、
[独立逐项检查](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/INDEPENDENT_LATEST_EVIDENCE_CHECK.json)
及 [独立签注](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/FINAL_INDEPENDENT_SIGNOFF_LATEST.md)。

精选当前文件 SHA256（完整 56 项见 manifest）：

| 当前文件 | SHA256 |
|---|---|
| web/app.js（包含导航修复） | e072c24dfeb5daa96ec7463745a0c6db827e53f2f3e5fe09114feb7a26cfd993 |
| reception/operations.py（在线备份与离线排他） | e81594948d34c2737fafa71bcc8bd1e312c7a35693dd05869227d240c7e32857 |
| reception/platform_fs.py（原生 SH/EX maintenance lease） | 2e42857bdc8c5f6bd9d894b3c430c5e04a02ef82240b36c92878d0a5a8c23325 |
| manage.py（备份预算接口） | 847b432ade56692b611278b7a04b0cb17d326bcb4f6adfa9ba29e98476109e6d |

### 修改前中间运行 — 历史位置保留

[full-final-01](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-01/regression/regression.json)
在 2026-10-10T05:21:50.581315+00:00 → 05:23:50.331158+00:00 实际记录
369 收集、367 执行、364 完整通过、3 部分、2 整项受限、0 失败/错误/线程异常，
55 个指定源码前后相同。其后真实 GUI 发现导航问题，用户补充在线备份并发/超时边界，
因此它不能作为最新代码的最终结果。
[原独立签注](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/FINAL_INDEPENDENT_SIGNOFF.md)
与旧 case/log/hash 原样保留。最终只选新 full-final-02 的 382 项，不叠加两次运行。

### 本轮发现的缺陷与实际关闭证据

严重度：P1 为可建立错误预约的歧义、更正及备份/破坏性维护协调缺口；
P2 为桌面输出/生命周期故障收尾、临时 sharing 冲突和浏览器导航。实际错误复现、
构造的故障注入与负向安全测试各自归档，未复现的原生 1177 不算通过。

| 问题与根因 | 实证及修复后行为 | 验证归属 |
|---|---|---|
| 预约更正同句含旧/新 service、area、time、contact 时，规则取 first match/配置列表先项 | 独立 pipeline 实际建立了错误待确认请求：Oakwood、Standard cleaning、冲突时间及 old@example.test。Root 在 model/engine 中对多个或否定候选保守转人工，在改变草稿/来源/期限之前停止；清楚的新单值仍可修正草稿 | 本轮独立业务 17 个方法全部通过；三种 booking 与 contact 的完整 before/after 序列保留，修后均无错误 pending 请求，provider 真实提交为零 |
| 桌面 ResultFile 在动作后验证，既有输出可导致“报失败但 app 已启动” | 独立安全 agent 实际复现 P2；Root/实现 agent 将原生路径验证及 CreateNew 预留移到动作前。拒绝既有、非法或 junction 输出，不创建 namespace、不启动 app | 当前完整运行中的 4 个桌面输入/失败边界方法通过，含真实子进程启动后的单次 receipt 写入故障注入与正常 owned stop |
| 启动后晚期收据冲突/写入失败需要清理真实子进程 | 从启动早期保存 app manifest/nonce；bounded 正常关闭按本次 run/PID/OS 创建身份执行，释放内核锁，不按端口或进程名强杀 | 14 个当前桌面进程方法及 20 个独立安全/运维方法在同一完整运行通过；注入 receipt 故障不冒充自然发生故障 |
| Windows manifest 原子发布遇真实 reader 临时禁止 delete sharing | 真实 deny-sharing reader 与 writer 线程构造竞争；reader 关闭后发布当前记录，不把短暂分享冲突误记成启动/停止失败 | 当前 3 个 demo/manifest 方法通过，包含真实 active-handler drain 与实际 reader sharing 竞争 |
| 篡改 manifest URL/port 不得让 launcher 去访问外部地址 | 只接受精确 numeric loopback URL/port，与该实例身份相符；错误 URL 在连接前拒绝，端口占用不认领或终止其他进程 | 当前桌面真实进程测试通过，网络策略没有被测试 bypass |
| 原 Windows evidence allowlist 不接受新批准的 final-acceptance 根目录 | 首次新入口实际拒绝并保留诊断；明确加入本轮批准根目录，保留专用目录、路径/reparse/无覆盖门禁 | 当前完整运行实际通过正式 Windows Test 入口，不将首次入口拒绝算作业务测试执行 |
| 真实 GUI 的品牌链接/浏览器导航改变 URL，却仍显示原 Business 页面 | Root 保留真实 before 截图与日志；`setView`/hashchange/popstate 使用规范路由，处理未登录/null state。Root 实际 Back/Forward/refresh 补测通过 | 导航修复已包含在选定 full-final-02 的 382 项源码哈希中；浏览器补测另列，冻结后 fresh GUI 核心 41＋入口 4＋恢复 7 已另行通过，不计入旧 369 或旧 41 |
| 在线备份缺少与 restore/delete/retention 的协调，SQLite backup 重试没有整体等待预算 | 用户允许在线备份；新设计 public backup 持原生 maintenance SH，破坏性离线操作按 service→maintenance EX；SQLite 只读源连接、progress/retry/verification 的 monotonic 预算，restore 内部 rollback 走已持 EX 的私有 helper | 新 13 个原生/SQLite 并发与超时方法在选定 full-final-02 全部通过，真实观察与原始收据在本节下方 |

独立原失败与中间诊断均保留，不覆盖旧日志：
[booking before](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/booking-reproduction-before.json)、
[booking after](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/booking-reproduction-after.json)、
[contact before](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/contact-reproduction-before.json)、
[contact after](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/contact-reproduction-after.json)、
[定向归属说明](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/TARGETED_RUN_ATTRIBUTION.md)
及 [独立安全复核](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-security/independent-security-review.json)。

导航真实发现与补修记录：
[before 截图](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/navigation-before.png)、
[before 日志](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/navigation-before-result.log)、
[after 日志](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/navigation-after-result.log) 与
[after 截图](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/16-navigation-after.png)。
在线备份设计、真实原生 lease 与 deadline 审查条件见
[独立设计复核](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/ONLINE_BACKUP_DESIGN_REVIEW.md)。
备份允许源正常业务写继续，不要求停机；restore、recover、delete、retention、restore acknowledgment
仍需明确停止写入并持离线排他。只读源指备份不作数据库写事务，不声称并发写源文件哈希恒定。
maintenance lease 协调本应用的操作，不能代表任意忽略该 lease 的外部 SQLite 工具；
普通同步文件系统/flush 调用不承诺可中断的硬实时墙钟上限。

本次选定 full-final-02 的 **13 个在线备份并发方法全部实际通过**。以下数值只取本次
[13 份原生/SQLite 收据](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/backup-concurrency/)，
不继承其此前定向数值：

- 真实 SQLite exclusive transaction：CLI .4 秒预算在含启动的 0.509254 秒后 exit 2，
  没有发布目标/残留 temp；释放后重试生成 verified backup，EX lease 可重获。
- 独立进程提交事务与 54,210,560 字节源快照重叠；128 对父/子行、commit ledger 同属
  generation 1，1600 bulk 行完整，integrity/foreign-key 检查通过，输出自含 DELETE journal。
- 高速 writer 的 stdout 持续后台 drain，272 次真实提交与 .8 秒预算重叠；实际观察到
  0.802485 秒 BackupTimeout、无输出/残留 temp、原生 lease 再获。没有声称读取了 SQLite
  内部 restart 计数或用假时钟/内核 mock 制造结果。
- 真实 backup SH 持有时，restore 等待 5.094569 秒后拒绝，无恢复 marker/rollback 修改；
  backup 仍活跃，SQLite 锁释放后成功。真实 retention 等待 5.087230 秒后 exit 2，
  源全文件哈希相同。
- 两个真实 SH 子进程并存，EX 被拒且 sidecar 无法删除；释放后 EX 可得。
  service lease 仍持有时，两个真实 backup 子进程成功，普通源写继续。
- 锁在期限前释放后成功、维护替换导致 source identity 变化时拒绝、无 IO 的非法预算、
  no-clobber 目标和 restore 内部 rollback 不重拿 SH 均通过；每份收据记录自己的合成 fixture
  已清理。

当前覆盖也包括：五 FAQ 逐项保存、重开、精确命中和禁用；未知咨询明确人工确认、
固定原资源期限及 human-only/noMarketing；重复接管/恢复、人工 event_id 去重；
新预约不继承旧字段与 pending_owner_confirmation；确定性 STOP-before-submit 和
already-in-flight 次序；SDK 签名/回调绑定、错误门禁/uncertain 不盲重试；
登录/CSRF/代理/异常输入；持久化去重、配额、隐私删除/保留和恢复复核；真实原生运维。
[完整 reviewed 覆盖矩阵](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/REQUIREMENT_COVERAGE_LATEST.md)
给出 canonical ID 和本轮 per-case 证据；重叠覆盖组不能相加为测试总数。

### 原 72 个跳过 ID 与仍受限的场景

| 原项/当前限制 | 本轮实际结果 |
|---|---|
| 原缺依赖的 45 SDK ID | 安装在已有项目 `.venv` 中的声明版本 9.11.2 实际执行，45 完整通过；只证明本地 SDK mock/合成签名，不证明真实 API/手机收发 |
| 其余原 27 ID | 本机原生可执行行为实际重测：24 完整通过、3 部分；Linux 分支没有由 Windows 结果继承通过 |
| 原 72 总计 | 69 完整通过、3 部分，无原 ID 缺失 |
| 4 个符链子场景 | 实际创建因既有 Windows 权限 1314 受限，分属 3 个 partial parent；未开启 Developer Mode 或改权限，junction 不代替符链通过 |
| 原 D 盘 8.3 方法 | 自身 fixture 没有既有短名，整项 skip 保留；新的 C 盘既有短名父目录另用真实第二子进程验证拒绝/释放后重获，不更改短名设置 |
| 客户私有目录/独立用户 token | 未提供合格条件，整项 skip；Windows 客户数据仍 candidate/blocked |

原原因、当前源位置/方法哈希、状态、子场景、case SHA256、退出码和日志在
[72-ID JSON](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/original-72-current-closure.json)、
[72-ID Markdown](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/original-72-current-closure.md)
及 [315-ID 差异](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/collection-delta-from-historical-315.json)。

真实 normal restore 的 DACL、owner、group 原始字节相同，但完整 SD control
由 0x8004 变为 0x8404（0x0400）；不能称完整 SD 全字节相同。真实 native sharing
error 32 保留原始完整 SD 与恢复证据。missing-name recovery 是真实文件移动构造的
状态，不证明错误 1177 自然发生。见本轮
[原生安全收据](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/independent-security-evidence/)
、[本轮只读权限/短名/token 能力探测](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-security/capability-01/capabilities.json)
与 [Windows 原生用例原始证据](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/independent-business/full-final-02/regression/security-evidence/)。
14 个桌面测试的最新辅助进程收据另存于统一 run 内新独占
[dsk-f02/proc-10359646](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/dsk-f02/proc-10359646/)，
属于新 382 完整执行。修改前 proc-cddf73ff 和 desktop/process-suite-final-04 继续保留为
各自运行证据，不代替当前统计。

## 4. 桌面快捷方式是否实际双击验证成功？

**Root 已实际验证首开、再次双击复用、真实浏览器 UI 退出及再次双击重启。**
当前用户真实桌面 `SMS Reception Assistant.lnk` 由桌面 ListItem 定位后通过
Win32 SendInput 发出两次真实鼠标点击，间隔 100 ms，系统双击阈值 500 ms；
不是用脚本 Launch 代替实体快捷方式操作。未关闭其他窗口或更改系统设置。

首开与第二次双击的状态记录保持同一 run_id
`b8e4fa95f065799e84321c52b4b9ffd4`；真实浏览器 UI 退出后 Root 再次双击，
记录新的 run_id `2e8fa1d06aabfc0c6d95b1d3d11427af`。地址都是
`http://127.0.0.1:8765`。这些交互由 Root 执行，独立 reviewer 只读核对了其收据，
没有冒充自行执行桌面鼠标或浏览器操作。

证据：[首次真实双击](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-first-double-click.json)、
[首次状态](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-first-status.json)、
[第二次真实双击](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-second-double-click.json)、
[复用状态](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-second-status.json)、
[UI 退出后的确认](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-first-exit.json)、
[重启真实双击](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-restart-double-click.json) 及
[重启就绪状态](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-restart-ready-status.json)。
首开/复用同一实例，新 PID/run ID 重启及最终冻结版本正常停止的统一核对见
[桌面总结](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-acceptance-summary.json)；[运行截图](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-running-demo.png)
仅截取受控演示页面，不包含其他应用。当前快捷方式的实际目标、参数和工作目录见
[安装收据](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/shortcut-installed.json) 及 [快速开始](WINDOWS_QUICK_START.md)。
最后一次冻结版本物理双击于 2026-10-10T05:55:39 UTC 后启动 run
`cd131799ef50fc6a193faf127319eb64`，实际 app PID 33404 / venv redirector PID 53844；
[就绪](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-final-source-ready.json) 与
[正常 Stop](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-final-stop.json) 确认 stopped/released。两个父子 PID 不算两份 app。


### 正式服务登录后新浏览器验收

本次使用实际 PowerShell Start → 项目 `.venv` → `app.py`、`APP_MODE=service`、
回环地址、独立合成库及隔离 Chrome 浏览器。禁真实发送，模拟来源仅是持久化合成
输入，禁止发送的 provider 不能提交短信；没有把加锁后的 UI fixture 当作入口验收。
最终冻结后重新执行 core / entry / restore，旧 41＋4＋7 不计入本次结果。

| 选定阶段 | 新断言数 | 内容 / 证据 |
|---|---:|---|
| 最终核心 4 阶段 | 41（18＋3＋13＋7） | 登录、人工核验/原期限/human-only、接管恢复、第五 FAQ、独立第二预约、待确认、STOP、队列暂停/取消、审计、CSRF 和退出；[阶段日志](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-final/) |
| 最终实际入口 | 4 | 第一次导航已记录、正式 app/service 登录和状态、真实发送关闭；[entry](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-final/entry-result.log) |
| 最终恢复 UI | 7 | 暂停与 review 双门禁、UI 不可提前恢复、后来 STOP 保留、零 provider SID、无 JS 异常；[restore](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-final/restore-result.log) |
| 同轮 5 FAQ 扩展 | 15（5＋5＋5） | 五项各自保存/刷新、独立新消息命中批准答案、逐项禁用状态保存（转人工/noqueue 见下行）；[配置](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/faq-configure-result.log)、[命中](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/faq-hit-result.log)、[禁用](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/faq-disable-result.log) |
| 同轮输入/权限/重复点击 | 15 | 过期与错误来源拒绝、不续期/不入队、required 空输入、真正 dblclick 人工消息只排一项、失败入站提示且正文遮挡；[日志](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/extended-gates-result.log) |
| 修后真实返回导航 | 4 | 品牌、Back、Forward、刷新显示与路由一致；[日志](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/navigation-after-result.log) |
| 独立空库与会话时限 | 9（6＋3） | 空列表、错误 FAQ 不保存；真实 60 秒 session TTL/等待过期、401 后重新登录；[空库](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/empty-login-result.log)、[过期](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/expiry-check-result.log) |
| 合计（不累计重复执行） | **95** | core 41 + entry 4 + restore 7 + extended 43 |

[结构化 UI 总结](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-acceptance-summary.json) 包含每条断言及日志 SHA256、
请求/响应与 console 分类。[最终核心 trace](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-final/trace.zip)、
[恢复 trace](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui-final/restore-trace.zip)、[扩展 trace](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/trace.zip) 和
[时限 trace](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/ui/expiry-trace.zip) 从首次导航记录。本次没有外部网页请求或未捕获
JavaScript runtime error；401 登录前/退出/过期、403 CSRF 是预期拒绝，favicon 的资源
错误另记。早期扩展日志的 400 属于保留的测试工具 payload/字段诊断，随后按真实
API contract 重测，并非修改业务门禁来让测试通过。trace 在终止采集时少数请求
status -1 表示记录尚未结束；完成的浏览器响应列表另记，不冒充应用失败或成功。

额外 UI 场景在同一验收轮中执行；其后只增加与其路径无关的在线备份修复。
核心/入口/恢复再次从新合成库在最终冻结版执行。会话过期使用真实墙钟；
服务咨询过期来源是离线持久化历史向前移 25 小时的**状态构造**，不是实际等了
24 小时、不是篡改系统时间，也不声称真实 provider 资源核验。
通过的人工审核保留原始截止，无营销许可或自动回复权限升级。

最终正式入口记录：
[第一次 Start](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/final-service-entry-01/launcher-ready.json)、
[第二次](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/final-service-entry-02/launcher-ready.json)、
[第三次](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/final-service-entry-03/launcher-ready.json)、
[恢复后第四次](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/final-service-entry-04/launcher-ready.json)。
每次保留 actual/redirector PID、真实 OS 创建时间、命令、app 日志、
run-manifest、launcher exit；均正常 stopped/released。第三次服务仍运行时通过
[在线 Backup](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/final-ui-online-backup/operation-result.json)，停止后进行
[Restore](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/final-ui-restore/operation-result.json)，再真实 Start 和登录验证恢复门禁。
冻结后的最终合成库 integrity/foreign-key 校验通过，16 项 outbox 均 canceled、
零 provider SID，见 [无客户内容的统计](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/synthetic-final-state-summary.json)。

### 环境、清理及最终版本

本轮重新查询 [环境与依赖](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/environment-final.json)：项目解释器/前缀
`D:\SMS Reception Assistant\.venv\Scripts\python.exe` / `D:\SMS Reception Assistant\.venv`；
base_prefix `D:\Anaconda`，Python 3.13.9 / AMD64 / 64 位，非全局包环境。
Python sqlite3.sqlite_version 与外部 `D:\Anaconda\Library\bin\sqlite3.exe` 查询分别记录，
均 3.51.0；Twilio 9.11.2，16 固定依赖实际检查/SDK 导入通过，新 `pip check` exit 0。
全局 Anaconda 的 Twilio 仍不存在。本轮没有安装依赖或改全局环境。Anaconda
real-location 警告仍如实保留，查询退出码为 0；不隐藏为“无警告”，也不据此改系统。
所有新 UTC 记录为 +00:00/Z，本地另标 America/Los_Angeles；原 UTC 文件未修改。

[清理结果](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/cleanup-result.json) 记录本轮明确拥有的临时数据库/备份/锁、
复制虚拟环境/fixture、隔离浏览器 profile 与身份指针，保留 logs/JSON/截图/trace；
只在已终结且身份确认的进程/目录上执行，不递归删除未授权目录，不结束用户服务。
快捷方式、项目环境/源码及可重新生成合成数据的 namespace 保留。
实际删除 349 个记录目标，失败 0；清理前后本轮相关进程均为 0，任务根及默认
三个会话的合成 DB/备份/锁已移除。清理后 [桌面状态](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/desktop-after-cleanup-status.json)
为 stopped，下次双击会创建新的合成会话。恢复/业务统计已在删除前独立记录。

最终所有源码/文档完成后生成 [after manifest](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/after-manifest.json)、
[改动摘要](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/source-changes.json)、[统一源码差异](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/source-changes.diff)、
[旧证据及历史正文保留检查](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/preservation-verification.json) 和
[交付链接/哈希核对](../artifacts/final-acceptance-runs/20261010T050317962Z-ea952dc6/delivery-verification.json)。
没有 Git 目录，差异来自本轮实施前副本；不是 git diff。

## 5. 哪些风险仍阻止商业交付？

**本地演示与离线工程验收不等同于已完成真实 SMS 交付或可安全保存客户数据。**
当前仍阻止相应商业里程碑的事项是：

- Windows 客户数据私有目录/跨用户访问控制未验收；实际目录权限和独立用户拒绝证据缺失。
- 已包含在商业范围内的受控真实收发测试尚未执行。先具备客户账号/号码资格与注册、
  participant 权限、批准 intake/callback 路由、精确正文/方向/条数/分段、币种/费用上限、
  时间窗口、停止与清理责任，并另行授权具体对外操作。应用配额不等于运营商账单硬上限，
  API accepted、sent、delivered 和参与者实际收到必须分别记录。
- Linux 本轮未运行；真实 Ctrl+C/Ctrl+Break 未运行；指定 mapped/network drive、
  UNC-target/未知 tag reparse 与 native error 1177 没有真实验证，不能用 mock 宣称关闭。
- 长期运行、监控、加密备份、保留期调度、旧备份/provider 数据清理、恢复演练、
  第三方费用与持续维护需明确责任及相应环境验收。

当前商业范围仍为 $350 / 10 天 / 1 次范围内修改、单商家、单个合格客户自有 Twilio 号码、
最多 5 个批准 FAQ、预约待 owner 确认、人工接管/相关回复、退订/审计、源码与经授权真实测试。
第三方费用由客户承担；不增加 AI/LLM、语音、CRM、日历预约、营销、多租户、
不限量或收益承诺。没有真实购买、账户设置、持续凭据、公网部署或发布。
具体真实测试边界见 [REAL_E2E_PLAN](REAL_E2E_PLAN.md)，运维责任见
[服务范围](SERVICE_SCOPE_REVIEW.md) 与 [运维说明](OPERATIONS_ZH.md)。

独立离线签注：independent_acceptance，实际执行与 382 份证据核对；
2026-10-10 UTC / 2026-10-09 America/Los_Angeles。
Root GUI、桌面、文档及清理汇总：本报告对应新结构化证据已完成；2026-10-10 UTC。
