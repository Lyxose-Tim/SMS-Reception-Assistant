# Windows 本地环境、前台入口与合成验收

保留现有 Python＋SQLite 项目。Windows 本轮首先提供项目隔离环境和真实 `app.py`
前台服务入口，并验证单实例、关闭与离线运维。当前客户数据目录的访问权限仍待
验收；不得把合成库成功启动称为已获准的客户运行环境或 production-ready。
Linux 路径继续保留，本轮是否实际测试以新的 Windows 报告为准，不沿用旧报告充数。

当前范围仍为 **$350 / 10天 / 1次约定范围内修改**：单商家、一个合格客户自有
Twilio 号码、最多五条批准 FAQ、预约待人工确认、接管及相关人工回复、退订日志、
源代码及授权真实收发测试。第三方费用客户承担；真实 E2E 仍未执行。

## 项目环境与依赖

本机已有解释器是 `D:\Anaconda\python.exe`。项目 `.venv` 与全局 Anaconda 包隔离；
命令直接使用 `.venv\Scripts\python.exe`，不激活环境、不修改 PATH 或执行策略。
PowerShell 入口要求 **7.5或更新**；本机已存在该版本条件，不安装系统软件。
没有 `py` 启动器也能使用此入口。

Twilio 保持项目声明的 `9.11.2`。官方版本元数据包含 Python 3.13 与通用 wheel；
兼容性由实际安装、`pip check` 和原 SDK 测试共同验收，不能仅凭元数据宣布通过。
参见 [官方 PyPI](https://pypi.org/project/twilio/9.11.2/)。

Windows Setup读取项目根 `requirements-windows.lock.txt`，固定本轮项目venv实际安装
的16个版本（包括Twilio及必要依赖），以便重建同一软件包集合；文件注明Windows/
Python3.13及捕获UTC。Linux的 `requirements-twilio.txt` 保持原声明，不强套Windows
解析结果。更新锁文件需要新的依赖、回归及来源记录，不能静默升级。

```powershell
Set-Location -LiteralPath 'D:\SMS Reception Assistant'
& .\scripts\windows.ps1 -Action Setup
```

Setup 在新 run 目录记录命令、UTC起止、输出及退出码；只安装已有声明依赖和其
必要依赖，使用官方 PyPI、wheel、隔离配置和不共享的缓存。校验 Python/pip 确实
来自项目环境；失败不退回全局安装。不会读取用户的额外 pip 索引或安装目标。
`start.bat` 是原演示入口，强制 demo 与真实外发关闭；服务验收使用下述 Start。

## 首实例：真实前台 service 入口

```powershell
& .\scripts\windows.ps1 -Action Start -Port 0
```

Start 使用项目解释器执行 **`app.py --db ... --port ... --run-dir ...`**，设置
`APP_MODE=service`，清除继承的 Twilio/live/旧应用配置；`ENABLE_REAL_SEND=false`。
sender 是合成 `+12025550100`，Account/Auth留空，应用不构造 Twilio REST client。
密码/HMAC仅为可公开的合成测试值，传入子进程，不存储真实凭据或更改父环境。

默认合成登录密码是 `Synthetic Windows password 123!`；HMAC为
`windows-synthetic-privacy-key-32-characters`。**这两个值仅用于合成验收**，不可
用于客户资料或真实运行。新服务库为空；不会自动创建演示联系人或批准 FAQ。

若验收需要预置合成来源，先在服务未运行时准备独立库，并写入
`settings.windows_acceptance_synthetic='true'`。再指定它和一个新空入口目录：

```powershell
$testRun = 'D:\SMS Reception Assistant\artifacts\windows-alignment-runs\你的新运行ID'
& .\scripts\windows.ps1 -Action Start -Database "$testRun\native-ui.db" -RunDirectory "$testRun\app-ui-entry" -Port 0
```

脚本拒绝没有合成标记的既有库。预置数据仅是离线输入，不是实际 provider 验证
或真实短信证据；它不增加模拟 API，也不代替服务入口的实际锁、登录和 worker。
Start 前台等待进程结束，不安装后台服务或计划任务。

打开 `launcher-ready.json` 中实际 `url` 并登录；该记录包括申请的 venv解释器、
实际 `sys.executable`、OS模块、launcher与app的独立 PID/父子关系/OS创建时间。
Windows venv redirector可能有两个PID，不能把launcher PID当成应用PID。
`run-manifest.json` 的逻辑启动UTC与OS创建时间分别记录，端口取应用实际监听值。

## 正常停止与异常退出的区分

前台 Ctrl+C 是提供的退出路径，但本轮真实控制台 Ctrl+C/Ctrl+Break 未验收；
本轮正常退出证据使用以下身份绑定的 Stop。另一个 PowerShell 窗口执行：

```powershell
& .\scripts\windows.ps1 -Action Stop -RunDirectory "$testRun\app-ui-entry" -TimeoutSeconds 30
```

Stop 检查该run ID、app实际 PID及OS创建时间，写入仅含 `run_id/pid` 的本地请求；
应用内部正常 shutdown/drain，然后关闭SQLite并释放实例锁。脚本等待明确的
`stopped/released` 记录。超时报告失败，**不强制终止或按端口/进程名清理**。
错误/复用PID和错误层对象不能用来停止实例。每次重新启动使用新的入口目录。

异常退出是单列的测试：只针对测试创建且记录身份的实际 app 进程，记录退出与
重新获得锁。强制退出不是正常Stop，也不能当作正常drain证据。数据库仍在使用或
停止尚未证实时，不执行恢复/清理，不解除门禁。

## 新离线回归与登录后 UI

```powershell
& .\scripts\windows.ps1 -Action Test
```

Test 为新的专属输出目录调用 runner；收集规范 `test.id()`，记录实际执行结果与
逐项 skip，保留原45个SDK ID及72个旧skip的映射。发现/收集/执行/通过/失败/错误/
skip分别统计；新增案例不代替原案例，不把重复运行相加。临时目录仅属于当前进程。

旧 **283项／211通过／72跳过** 和旧UI **41断言** 有原日志及截图，但不能作为本轮
通过。原报告 timestamps 原文是UTC `+00:00`；PowerShell先前的本地DateTime显示
不构成原证据错误。新证据记录UTC与独立本地时区，不覆盖旧文件或外部基线。

正向登录后核心 UI 必须来自上述真实 Start → 项目venv → `app.py` 入口，检查
登录、未知咨询明确确认及人工入队、来源/原截止、接管恢复、FAQ编辑、预约待确认、
退订阻断、队列暂停/取消、审计、隐私、CSRF及登出。隔离合成数据库与浏览器；
外发保持关闭，记录实际路由/交互、截图、错误与正常停止。辅助fixture覆盖不替代
真实首实例入口证据。

## 合成备份、恢复与保留期入口

以下命令只接受合成标记库和项目内路径，使用真实 `manage.py` 运维实现；不能用来
操作客户库。它们不绕过同一实例/恢复锁、完整性校验或恢复审查。

Backup 可以与正常业务并行，以 SQLite backup API 取得一致快照，maintenance 共享锁
防止与恢复/删除/清理冲突。Restore、Maintenance、AcknowledgeRestore 仍要求停服
及 service→maintenance 排他锁。备份默认整体等待/SQLite 工作预算 5 秒，超时失败
且不发布目标；如需选择预算，项目 `.venv` 直接调用 `manage.py backup --timeout-seconds`
（大于 0、不超过 120 秒）。PowerShell `-TimeoutSeconds` 不改变备份预算。
同步操作系统调用不承诺硬实时中断。实际并发与超时证据见
[最终验收报告](FINAL_ACCEPTANCE_REPORT.md)。

```powershell
& .\scripts\windows.ps1 -Action Backup -Database "$testRun\native-ui.db" -RunDirectory "$testRun\backup-run"
& .\scripts\windows.ps1 -Action VerifyBackup -BackupPath "$testRun\backup-run\verified-backup.sqlite3"
& .\scripts\windows.ps1 -Action Restore -Database "$testRun\native-ui.db" -BackupPath "$testRun\backup-run\verified-backup.sqlite3" -OfflineConfirmed
& .\scripts\windows.ps1 -Action AcknowledgeRestore -Database "$testRun\native-ui.db" -OfflineConfirmed -ReviewConfirmed
& .\scripts\windows.ps1 -Action Maintenance -Database "$testRun\native-ui.db" -RetentionDays 30 -OfflineConfirmed
```

恢复/维护前停止所有数据库使用者。恢复后默认停发且需独立复核；ack只确认复核，
不会恢复发送或重放旧队列。Windows恢复若保留了待核查事务记录，不擅自删除文件，
按 `OPERATIONS_ZH.md` 的恢复步骤处理。访问权限、reparse/别名、文件占用与替换
失败需真实平台证据；`chmod`成功不是Windows访问控制证明。

## 边界、证据和维护责任

新产物位于 `artifacts/windows-alignment-runs/<运行ID>/`，本轮独立验收也允许
已批准的 `artifacts/final-acceptance-runs/<运行ID>/`。已有证据不覆盖；特别保护
`artifacts/windows-alignment-evidence/`、旧current回归和 `output/playwright/`。
新独立实际结果见 [FINAL_ACCEPTANCE_REPORT.md](FINAL_ACCEPTANCE_REPORT.md)；
[WINDOWS_TEST_REPORT.md](WINDOWS_TEST_REPORT.md) 是前轮历史续篇。Linux未实际
执行的项仍标未验证。客户数据目录ACL/
私有存储条件未获验收时，保留候选状态；不自行改ACL、开发者模式或系统设置。

客户/指定维护人负责长期加密备份、保留期调度、旧副本/provider数据清理、恢复
演练、监控、费用与故障处理；持续维护另定协议。合成临时进程、库及profile清理
只限已记录的本次对象；日志与脱敏证据保留，假名化安全记录另列。真实E2E仍需
账号/号码资格、注册、参与者、精确正文/方向/条数/分段、回调条件、费用、时间及
停止/清理授权；本轮不发送、部署或发布，也不增加LLM、语音、CRM、日历或多租户。
