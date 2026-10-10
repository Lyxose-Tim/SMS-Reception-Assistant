# 历史原生对齐报告及最新续篇索引

本文件下方完整正文记录此前 315/310/3/2 及 UI 41＋4＋7，不是本次新验收统计。
最新最终文件选定 **382 收集、380 执行、377 完整通过、3 部分、2 整项受限，
0 失败/错误**；新正式服务 GUI 和桌面物理双击已另行验证。
当前结果见 [FINAL_ACCEPTANCE_REPORT](FINAL_ACCEPTANCE_REPORT.md)，
主索引见 [TEST_REPORT](TEST_REPORT.md)。历史日志、72 矩阵及哈希全部保留。

---

# Windows 原生对齐验收 — 2026-10-09 本地日期

已按原生 Plan 的范围审阅与“是，实施此计划”批准实施。本轮使用现有项目作增量修改，
未重写或恢复旧源码。Windows 本机的回环合成服务、真实进程互斥、运维和登录后 UI
已有新证据；**客户数据运行仍为候选**，私有目录及跨用户访问证明尚未提供。
Linux 路径保留但本轮未运行；真实 SMS E2E 未执行。没有 production-ready 声明。

## 原始计划、批准及证据归属

- [原生 Plan 原文](WINDOWS_ALIGNMENT_PLAN.md)保留计划时“实施未开始”的历史状态；
  当前实施状态及批准依据见
  [implementation-status.json](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/implementation-status.json)。
- 外部原生批准记录仍在
  [NATIVE_APPROVAL_CONFIRMED.json](../artifacts/windows-alignment-evidence/NATIVE_APPROVAL_CONFIRMED.json)，本任务不写入该目录。
- [TEST_REPORT.md](TEST_REPORT.md)顶部同步本轮结果和续篇索引，下方完整保留上一轮正文。
  旧 283/211/72 和旧 UI 41 项是历史证据；原独立 Windows 218/197/1/20/0 与原 Linux
  218 通过也是各自运行的记录，不能计入本轮。
- 原 `offline-regression-current.json` 的时间原文是 `+00:00`；此前 `-07:00` 是
  PowerShell 读取显示转换。本轮没有修改原文件或寻找不存在的错误源记录。
- 项目没有 `.git`，未声称审查 git diff。使用实施前源码、文件清单和 SHA256，见
  [before-manifest](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/before-manifest.json)、
  [改动清单](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/source-changes.json)和
  [统一源码差异](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/source-changes.diff)。
  [实施后清单](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/after-manifest.json)
  在全部源码、主报告及文档完成后生成；[旧正文保留证明](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/historical-report-body-preservation.json)
  记录原正文作为准确字节后缀保留。

| 改动文件组 | 修改摘要 |
|---|---|
| `reception/platform_fs.py` | Windows内核锁、路径身份、驱动/NTFS/逐层reparse预检、原生无覆盖移动/替换/flush；保留Linuxflock |
| `app.py`、`reception/service.py`、旧UI fixture | 所有持久化入口先锁；启动失败/drain/停止资源边界，真实PID/run_id；fixture改新独占目录，不代替正式入口 |
| `reception/operations.py`、`manage.py` | 可靠备份、停写恢复、安全状态合并、rollback/marker/recover-restore、删除及维护路径 |
| PowerShell、bat、Windows依赖锁 | 项目venv隔离、前台service、正常stop、独立日志及离线运维；固定实际16版本 |
| 原测试与Windows新增测试 | 保留原72-ID/断言，实际子进程、文件占用、路径无副作用、DACL原始对比、worker失败；能力子场景明确partial |
| README、主报告、实施复核、指南/交付文件 | 同步当前证据与历史归属、候选平台及商业边界；不新增业务功能 |

## 实际环境及隔离依赖

| 项目 | 本轮实际值 |
|---|---|
| 系统 | Windows 11，build 26200；D: 本地固定 NTFS |
| 已有基础解释器 | `D:\Anaconda\python.exe`；3.13.9、AMD64 / 64 位 |
| 项目解释器 | `D:\SMS Reception Assistant\.venv\Scripts\python.exe` |
| 虚拟环境 | prefix 为项目 `.venv`，base_prefix 为 `D:\Anaconda`；不继承系统 site-packages，user site 关闭 |
| Python SQLite | `sqlite3.sqlite_version == 3.51.0` |
| 独立 SQLite 工具 | `D:\Anaconda\Library\bin\sqlite3.exe -version` 单独得到 3.51.0 / 64 位，不代替 Python 查询 |
| SDK | 官方 PyPI 安装 `twilio==9.11.2`，导入来自项目 `.venv`；`pip check` 无损坏依赖 |
| 复现依赖 | [requirements-windows.lock.txt](../requirements-windows.lock.txt)，16 个实际解析版本；Linux 保留原声明文件 |
| PowerShell | 7.6.5；现有 LocalMachine RemoteSigned，本轮未修改执行策略 |
| 时间 | 完整运行使用 UTC `+00:00`；本地时区 America/Los_Angeles，另列本地偏移 `-07:00` |

[实际解释器/导入来源](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/environment.json)、
[安装完整日志](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/dependency-install.log)、
[依赖完整性](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/pip-check.log)、
[最终环境查询](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/final-environment-command-results.json)和
[只读磁盘/策略查询](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/final-volume-policy.json)
记录命令、结果和时间。基础 Anaconda 中 Twilio 仍未安装；不改变 PATH、其他环境或系统软件。
安装使用项目进程专用临时目录和官方注册库，没有全局/用户级回退。

受限工具环境的 GetVolume / GetFinalPathName 访问拒绝属于工具环境差异；经工具层批准后，
原生测试在同一本机对本任务合成文件实际执行。未绕过审批或降低安全门禁。

## 选定完整运行及统计

选定目录为
[`full-windows-03`](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03/regression.json)，
命令为项目 PowerShell `-Action Test -RunDirectory ...\full-windows-03`；内部实际调用
项目 `.venv` 的 `scripts/run_offline_regression.py --output-dir ...`。
开始 **2026-10-10T04:27:00.958186+00:00**，结束 **04:28:18.573756+00:00**；退出码 **0**。

| 统计 | 数量 |
|---|---:|
| 发现 / 收集 / 选择方法 ID | 315 |
| 实际执行方法 | 313 |
| 完整通过 | 310 |
| 部分执行：共享断言通过、权限子场景受阻 | 3 |
| 测试失败 / 错误 | 0 / 0 |
| 整项 skip | 2 |
| 受限子场景 skip | 4 |
| 未捕获后台线程异常 | 0 |

标准 unittest 的 `skipped=6` 是 **2 个整项 + 4 个子场景**，不是 6 个整项。
3 个 partial 不冒充完整通过；310 + 3 + 2 = 315，实际执行 310 + 3 = 313。
原 72 个 skip ID 全部仍在：**69 完整通过、3 部分执行**，无缺失；45 个缺 SDK 的
原 ID 全部实际通过。[72-ID Markdown](WINDOWS_SKIP_MATRIX.md)与
[完整 JSON](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/skip-closure-72.json)
保留原原因、分类、源码位置、适用平台、子场景、退出码、日志和用例 SHA256。

收集从 283 到 315：新增 2 个 worker 失败处理测试、13 个原生生命周期测试和
17 个 Windows 运维测试，共 32 个。另将一个**原本通过且不属于 72 个 skip**的
“Windows 必须拒绝”测试映射为不支持平台时拒绝的测试，因此差异列表为 33 added、
1 removed，净增 32。[逐 ID 差异与语义映射](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/collection-delta.json)
没有删除、改名或弱化原 72 项来制造通过。

完整原日志见 [regression.log](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03/regression.log)，
父启动 stdout/stderr 见 [test-launch.log](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03.launch-temp/test-launch.log)。
SDK mock/合成签名测试保持原断言；runner 拒绝非回环连接，没有测试专用发送权限绕过。

其他运行单独保留，不能累加数量：

| 运行 | 归属 |
|---|---|
| 原 45 SDK 定向运行 | 45/45 通过，属于本轮中间验证；当时全局发现258是运维模块生成中的中间收集，仅选择45；最终315已恢复完整ID，不把缺项计通过 |
| `full-windows-02` | 314项的干净中间运行；此后修复demo入口，加1实际子进程测试；最终只选full03，不累计 |
| `full-windows-01` | 原生断言计数完成，但 4 次 cmd 输出捕获出现后台 UTF-8 解码异常；保留完整诊断，未选作最终干净结果 |
| `security-raw-equality-diagnostic.txt` | 17 项定向运行有 1 个过强“整个 SD 字节必须不变”断言失败；实际 Windows 增加继承标志，原始失败证据保留 |
| `security-comparison-final-verified.txt` | 精确 DACL/owner/group 及描述符差异测试修正后 16 通过、1 受阻；最终整套亦执行同样断言 |
| 首次 SDK 收集预检 / UI CLI 兼容诊断 | 未执行业务用例的编码/脚本上下文诊断，不能当作业务失败或通过 |

`mklink` 已改为原始字节捕获，保留 hex 与生产者说明；没有替换乱码或吞异常。
runner 将未捕获线程异常纳入退出失败门禁。最终完整运行 stderr 为空。

## 原生互斥、恢复、路径及故障证据

Windows 使用真实 `LockFileEx`，Linux保留 `flock`；先取得共同数据库排他再初始化。
保留单进程、多个有界 HTTP 线程、一个发送 worker。按规范数据库路径与稳定 sidecar
协调服务、恢复、维护和清理；当前进程对象不是跨进程互斥证据。

[完整 native-evidence](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03/native-evidence/)
逐父 ID 记录命令、launcher/app PID、OS创建身份、返回码、stdout/stderr、最终 manifest。
实际验收包括首实例登录、第二实例拒绝且数据库内容不变、正常关闭后重启、仅测试创建
进程的异常退出后重启、端口绑定失败、worker 启动失败后数据库/锁/端口重取、路径别名、
持锁 sidecar 拒绝删除/改名、服务与运维互斥。

worker 启动失败用例只注入该 worker 的 `Thread.start` 异常；文件、内核锁、数据库、
端口和后续正常 CLI 都是真实操作。它是**故障注入**，不冒充自然发生故障。
正常首实例正向启动始终是未包装的项目 `.venv` **app.py / APP_MODE=service**。

最后入口复核发现demo指定活动service库可绕过锁，已在所有持久化app模式创建Engine前
共用锁及marker；demo的`:memory:`保持原行为，service拒绝内存库。新增真实demo子进程
被活动service锁拒绝，数据库整文件及全部表内容哈希不变，正常stop后锁重获。
旧UI fixture也使用共同锁和新独占输出目录，移除import时目录副作用；本轮未运行它。

备份使用真实 SQLite backup API，包含已提交 WAL，校验完整性及外键后关闭句柄，
原生无覆盖发布。恢复先停写、排他、保留独立验证 rollback，再合并安全状态、冻结工作，
checkpoint/关闭句柄后原生替换。保留暂停和复核；marker 未决时阻断启动/进一步破坏性操作。
无效备份、真实文件占用、WAL reader、并发维护、未知恢复状态与句柄关闭均实际测试。

**最新复核 A：真实只读比较安全描述符/DACL。**

| 场景 | 实际观察及归属 | 原始证据 |
|---|---|---|
| 正常 restore | DACL、owner、group 原始字节一致；整个 SD 不完全相同，仅 `0x8004→0x8404`、xor `0x0400`（SE_DACL_AUTO_INHERITED），offset 3 改变 | [normal comparison](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03/security-evidence/normal-restore-7eb3d502e549/comparison.json) |
| missing-name rollback | 通过真实原生移动构造缺失原名称状态，再实际 recover；SD/DACL/owner/group 全部字节相同 | [rollback comparison](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03/security-evidence/missing-name-rollback-f66f4c777511/comparison.json) |
| 原生 sharing violation | 持有真实排斥句柄，ReplaceFileW 实际返回 Windows 错误 32；原库与恢复后 SD/DACL 全相同 | [failure comparison](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/full-windows-03/security-evidence/native-sharing-failure-32-188a2605b9e1/comparison.json) |

JSON 指向 before/after 原始 bin、长度和 SHA256；没有归一化成“全部相同”。未调用
ACL setter，未改 SACL、权限或用户。**错误 1177 未实际复现**；构造其文档描述的文件
状态只证明恢复分支。AccessCheck 仅证明当前 token 访问，不证明其他用户被拒。

**最新复核 B及原备份副作用问题：** 先纯文本拒绝 UNC、扩展 UNC、ADS、设备、
drive-relative、尾空格/点等；再 GetDriveType 固定盘/NTFS检查；按已经验证的物理父目录
逐组件 nofollow 打开。中间 reparse 先读取存储目标、检查语法/驱动，再递归检查本地目标。
未知/远程/无法证明安全的边界拒绝。真实本地 junction 别名仍可共用既有数据库锁；
经 reparse alias 创建缺失后缀拒绝，不能误写为“全部父组件都不是 reparse”。
非法备份输入实际 native-open=0、目录树内容及 mtime/ctime 不变；合法嵌套目录备份实际成功。

没有创建网络共享。真实 mapped-drive、UNC-target junction、未知 reparse tag 没有
获准测试条件，因此列为未验证；不把本地 audit 当作这些场景已实际通过。
路径检查依赖受控目录，不能替代客户目录 ACL 或抵抗可并发修改目录的其他账户。

## 正式入口及新登录后 UI

实际使用 `scripts/windows.ps1 -Action Start` 启动项目 `.venv` 的 `app.py`，
`APP_MODE=service`、回环、发送关闭、Account/Auth空、没有 REST client。数据准备器
只在停止后持真实锁预置合成来源，不构造 HTTP fixture。三轮独立预约/FAQ/STOP UI
启动，加一轮恢复后的启动，全都通过项目 Stop 正常排空，退出 0、锁 released。
选定最终版本的入口05/06/07/09与ui-final；08因验收脚本端口门禁未同步而先中止，
其诊断日志保留，修正门禁后09重新通过，不属于应用故障；先前01–04/UI结果只作中间证据。
manifest 区分 venv redirector 与实际 Anaconda image 子进程，不能混淆 launcher PID。

核心 UI **41 项重新通过**，另有 **4 项入口断言及 7 项恢复 UI 断言**，按用途分别列。
没有复用旧结果。覆盖未知咨询明确人工确认、限时权限快照、接管恢复、五 FAQ 编辑及
实际回复、第二请求新字段与 pending_owner_confirmation、STOP取消/禁人工回复、
队列取消/暂停、审计、隐私开关、CSRF 403、登录退出 401 和可读错误提示。
恢复 UI 实际显示复核警告，发送恢复按钮禁用；显式离线复核后数据库仍暂停。

[新 UI 汇总](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/ui-final/ui-acceptance-summary.json)、
[首次导航起的 trace](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/ui-final/trace.zip)、
[恢复 trace](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/ui-final/restore-trace.zip)、
[恢复界面截图](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/ui-final/11-restore-review-required.png)和
[最终正式应用日志](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/app-ui-entry-09/application.log)
保留交互、路由、截图和拒绝。没有 JS runtime error、外部网络请求或 provider提交。
登录前/退出后的401、故意错误登录401和CSRF403分别归类；favicon无请求。
HTML password autocomplete提示为浏览器 verbose建议，不冒充应用运行错误。

PowerShell Backup、VerifyBackup、Restore、Maintenance、AcknowledgeRestore 也实际执行，
完整 [operator日志](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/ps-final-restore/operation.log)
及各自 operation-request/result 记录退出0。先前未捕获 stdout 的三次步骤保留诊断，
不伪造旧日志；最终新增 captured 目录重新验证日志。

## 受阻条件、支持判定与清理

| 平台/条件 | 判定 |
|---|---|
| 本机 Windows11 / Python3.13.9 / 本地 NTFS / 合成回环服务 | 启动、进程互斥、运维、核心回归与UI实际验收通过；只覆盖本报告配置 |
| Windows 客户数据 | **候选/受阻**：当前项目 ACL 宽；缺私有目录及获准独立用户 token，当前 token 通过不能证明跨用户私密 |
| 原文件/目录 symlink子场景 | 4个实际创建因WinError1314受阻，未开Developer Mode或改权限；原断言保留，不用junction代替 |
| 8.3 alias | 本卷没有现存短名，整项受阻，未改8.3设置 |
| 网络驱动/UNC reparse/未知tag | fail-closed实现保留，真实场景未验证；不是支持的运行目标 |
| Linux | 保留部署选项、flock及0600等断言，本轮未执行，无新Linux通过声明 |
| 真实 E2E | 未执行，需另行完整授权；不表示已交付真实收发或运营商批准 |

[清理记录](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/cleanup-final-result.json)
证明最终3个隔离profile和合成库/backup/rollback/lock已删除，操作前核对绝对路径、
无reparse和进程退出；没有强杀进程。保留源码、项目依赖、日志、截图、trace及只读SD证据。
[删除前最终状态](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/cleanup-final-state-before.json)
证明 provider提交0、全部16任务已取消、STOP保留、复核后仍暂停。

外部62份 baseline-source快照、旧回归原JSON/log、旧报告、旧UI及用户数据未被覆盖。
实施前保护清单130个比较项中129个相同；唯一变化是外部 `STATUS.json` 在批准时
03:39:11更新（实施前像03:39:04），本任务只观察并保留，未恢复或写入。详见
[保护证据对比](../artifacts/windows-alignment-runs/20261010T033904198Z-f42f3b72/protected-evidence-verification.json)。

商业范围仍为 **$350 / 10天 / 1次范围内修改**、单商家、单个合格客户自有Twilio号码、
最多5批准FAQ、预约待确认、人工接管与回复、退订日志、源码及经授权真实收发测试；
第三方费用由客户支付。不增加AI、语音、营销、CRM、日历或多租户。
持续维护另定协议；客户/指定维护人负责长期备份加密、保留期调度、旧备份/provider数据
清理、监控和费用。交付方提供操作说明、演练与本次临时材料清理证据。

真实测试的账号/号码资格与注册、参与者权限、精确正文、方向/条数/分段、回调环境、
费用币种/预算、时间窗口、停止及清理责任仍须单独批准，见 [REAL_E2E_PLAN](REAL_E2E_PLAN.md)。
本轮没有持续真实凭据、公网开放、部署、发布、注册/购买、付费资源或真实收发。
