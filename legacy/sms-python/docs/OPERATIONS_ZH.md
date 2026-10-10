# 本地运维、部署准备与回滚

本版是单商家、单个客户自有 Twilio 短信号码的实现及部署模板。默认 dry-run；本地和 mock 测试不证明真实送达。**真实联调待完成，不得宣称 production-ready。** 本文没有执行公开部署、购买号码、订阅、充值或建立持续账户访问。

当前范围为 **$350 / 10天 / 1次约定范围内修改**，最多五条批准 FAQ、待人工确认
预约、接管/人工回复、退订审计日志、源代码与授权真实收发测试，第三方费用客户
承担。测试包含在当前范围，但不自动授权凭据配置、公网暴露、真实发送或部署。
Windows专项增加真实前台入口、原生锁与合成运维验收；客户资料目录ACL/私有存储
仍待验收，保留候选状态。Linux单实例路径继续保留，本轮未实际跑的Linux项不计通过。

## 安全配置

- 服务模式仅在服务端读取环境变量。真实凭据放在项目目录以外的安全配置或秘密管理系统；例如 `/etc/harbor-home/reception.env`，所有者 root、权限 `0600`。数据库目录建议权限 `0700`，文件 `0600`。环境文件、数据库、备份和日志不得进入源码、ZIP、浏览器或聊天。
- 创建后台密码哈希：在客户自己的终端运行 `python3 manage.py password-hash`，私密输入两次至少 12 字符的密码。命令不接受密码参数，仅输出带随机盐的 scrypt 哈希；将哈希安全写入 `ADMIN_PASSWORD_HASH`。
- `PRIVACY_HMAC_KEY` 使用客户终端的 `secrets.token_urlsafe(48)` 生成，安全保存到服务端环境。不要在聊天中传递它。必须长期稳定保存并与备份安全保管；丢失或擅自更换会使历史退订哈希无法匹配。哈希不是加密，也不是匿名化。
- `APP_MODE=service`；`ENABLE_REAL_SEND=false`。真实外发必须在账户、号码、注册、实际退订配置、测试参与者同意和逐条发送计划获准后，才单独启用。入站咨询只用于相关服务回复，不创建营销许可。
- 默认 `RECIPIENT_POLICY=test_allowlist`；真实受控测试保持此策略。另行明确开启
  `inbound_service` 需对应 `INBOUND_SERVICE_APPROVAL_RECORDED`，只允许已核验、
  未过期来源支持的相关服务回复。未知咨询由人工审查并明确确认，记录来源与原
  截止，只建立人工回复依据；重复确认不会续期。后台显示策略，不提供开启控制。
- `RETENTION_DAYS=30` 默认保留期；支持 1–365 天。配置保留天数不会自行执行定时删除；需按客户确认的计划运行维护命令，或另行授权配置运维调度。应用限额不是运营商账单硬上限。
- `TWILIO_PUBLIC_BASE_URL` 是实际外部 HTTPS origin，与 Twilio 配置一致。验签使用固定外部地址、原始请求路径和 query、全部表单参数。不要用未经信任的 `X-Forwarded-*` 值拼接验签 URL，不要重写路径、参数或编码。

## 部署模板

`deploy/harbor-home.service` 和 `deploy/nginx.conf.example` 是未安装的Linux模板。
Windows使用 [WINDOWS_GUIDE.md](WINDOWS_GUIDE.md) 的真实前台PowerShell入口，不安装
服务或任务；macOS未验证。Windows启动/互斥/关闭/备份恢复结果以新报告实际证据
为准，不能继承旧Linux报告。

Python 服务只监听 `127.0.0.1:8765`，仅允许单进程、单实例 SQLite。服务持有 `数据库路径.service.lock` 独占锁；禁止多个 worker 或多个应用实例共用数据库。TLS 终止在 Nginx。公开访问仅开放 `/webhooks/twilio/sms` 和 `/webhooks/twilio/status` 的 POST；后台额外限制为企业主的 VPN/IP 范围，并要求应用登录与 CSRF 校验。模板后台默认 `deny all`，须填写已批准地址。模板中的示例域名和证书路径不可直接使用。

代理保留原始 query，不配置 `proxy_pass` URI 后缀或 rewrite。关闭包含 query、消息正文、Authorization 或 Cookie 的访问日志；同样审查负载均衡、APM、错误追踪和备份软件的日志设置。模板禁用 access log，并将 error log 写至 `/dev/null`，防止 upstream 错误把 callback nonce/query 写入日志；需另行配置经审查的元数据健康监控。有效签名的 webhook 也必须通过账户、号码和资源核验，不能仅凭公开 URL 放行。

部署、域名、证书、服务器资源、管理员账号和持续凭据权限均需另行获得授权；模板本身没有建立任何这些资源。

## 紧急停止与重启

优先在后台暂停 outbox；也可创建 `EMERGENCY_STOP_FILE` 指向的服务端文件。外发
前重查退订、运营商阻断、人工接管、所选收件人策略、来源截止、速率、分段和紧急
开关。STOP 入库、撤销/取消队列与最终发送提交共用本地顺序边界：退订先提交时，
业务发送不得开始；以退订事务成功 commit 和成功接受新 STOP 为边界，此后禁止
新的业务提交。正在进行的发送可能在该 commit 前完成；不保证等待中的 STOP 优先
获得锁，也不把原始请求到达时间当作退订已完成。等待有界，超时返回可重试失败，
不确认接收成功，需由 webhook 重试后成功接受。应用不能撤回平台已接受的短信，
也不承诺运营商端到端绝对一次送达。原生独占锁排除第二服务进程；禁止多个实例/
worker 共享该数据库。

Windows入口记录venv launcher和实际app PID、OS创建时间及run ID。普通Stop仅向
正确实例写本地请求，由app完成shutdown/drain、DB关闭及锁释放；超时仍标未完成，
不强制终止或按端口清理。异常退出是单列测试，不是正常停止证据。服务仍运行、
关闭未确认或恢复事务未核实时，不执行破坏性维护。

进程正常重启后，未提交的持久化 queued 任务仍逐项经过发送限制；崩溃时处于 submitting 的任务变为 uncertain（待核实），不自动重发。遇到请求超时且无法判断是否提交时，同样保持 uncertain。只能对已知 MessageSid 读取 provider 状态；没有 SID 时人工核查，不能靠正文或时间猜测并重发。

## 备份

备份允许与正常业务写入并行，无需为在线一致快照停服。实现使用 SQLite backup API，
包括已提交但仍在 WAL 内的数据；不直接复制使用中的主数据库文件。完整性和外键
校验完成后无覆盖发布，Linux 文件权限为 `0600`；Windows 权限说明见下文。

公共备份持原生 maintenance 共享锁；恢复、恢复状态处理、删除、保留期清理及恢复
复核先取 service 排他锁，再取 maintenance 排他锁。两份备份可以并行，服务业务
写入可以继续，备份与这些破坏性操作不能重叠。所有入口必须遵守同一协议；任意
外部 SQLite 工具绕过该锁协议不属于受支持运维路径。

`manage.py backup --timeout-seconds` 默认 5 秒，允许大于 0 且不超过 120 秒。
单调时钟预算覆盖等待维护锁、SQLite BUSY/retry、复制及校验，超时明确失败，
不发布目标并清理本次临时文件，已有目标始终保留。同步系统调用、文件 flush
不能保证可中断的硬实时上限；大库应根据实际演练选择预算。PowerShell Backup
使用同一实现的默认 5 秒预算，PowerShell `-TimeoutSeconds` 是入口停止/启动等待参数，
不能将它当作备份复制预算。

本轮并发 writer、持锁等待、超时、释放后重试及正式服务运行中备份的实际结果见
[最终验收报告](FINAL_ACCEPTANCE_REPORT.md)，不是旧报告或 mock 通过。

```bash
python3 manage.py backup --db /var/lib/harbor-home/service.sqlite3 --output /secure-backups/harbor-home-20261009.db --timeout-seconds 5
python3 manage.py verify-backup --backup /secure-backups/harbor-home-20261009.db
```

这不是加密备份。客户应使用操作系统磁盘加密、加密备份服务或独立审查的加密工具，配置访问权限、备份保留期和恢复演练。备份包含客户信息；删除在线数据不会自动删除旧备份、Twilio 数据、操作系统快照或 SSD 历史副本。

上述 `0600/0700` 是Linux权限说明。Windows不能以chmod/stat模式位证明隔离；客户
资料、备份及密钥目录必须有另行核实的ACL/私有存储条件。本轮不自行更改ACL或启用
开发者模式；未取得证据时保持客户数据运行blocked。合成PowerShell Backup/Verify/
Restore/Maintenance入口和范围限制见Windows指南。

交付包含已有命令、说明和合成数据演练；客户或指定维护人负责长期加密存储、
备份调度、备份保留/删除、恢复演练、费用和故障处理。持续维护须另定范围与协议，
不附带订阅、24/7 支持或自动调度。回滚快照也包含客户数据，须纳入同一访问权限与
备份清理政策；不根据时间戳擅自删除未审查的唯一恢复副本。

## 恢复与回滚

先暂停外发并启用紧急停止文件；停止应用和所有访问该 SQLite 的其他工具。恢复命令要求明确 `--offline-confirmed`，还会检查应用锁与外部 SQLite 写锁。它不能可靠列出所有外部 WAL 读者，因此人工停止外部数据库工具是必要条件。

```bash
python3 manage.py restore --db /var/lib/harbor-home/service.sqlite3 --backup /secure-backups/harbor-home-20261009.db --offline-confirmed
```

命令先验证源备份，再生成原数据库的 `.rollback-时间戳` 完整快照；写入临时恢复文件、校验后通过原子替换安装。恢复过程将当前数据库中的退订 HMAC、事件/SID 去重哈希、无客户内容的用量账本与旧备份做保守并集，也为当前尚未删除的事件生成哈希，不复制当前客户内容；无法校验这些安全记录时拒绝安装。保守合并可能继续阻断后来已 START 的号码，必须人工核查。恢复后的 outbox 始终暂停且设置独立的 `restore_review_required` 门禁；普通后台恢复发送按钮不能绕过它。备份中的 queued 任务取消并标记 `RESTORE_REQUIRES_REVIEW`，submitting 变为待核实。尚未处理或核验中的 provider inbox 冻结为 rejected，防止恢复旧快照后自动重复回复。原数据库旧 WAL/SHM 不会应用到恢复文件。

启动后核对配置、数据库版本、退订状态、记录与已知 MessageSid；人工逐项核实备份之后发生的实际发送和退订。恢复旧快照可能丢失快照之后的新状态，所以**不能仅依据恢复后的数据库直接恢复外发**。核实并补全最新退订及 provider 状态、确认无需重发、重新获得必要授权后，再次停止服务，在本机执行以下确认。它只解除恢复审查门禁，仍保持 outbox 暂停；之后再单独核对紧急停止与发送授权开关。回滚使用生成的 rollback 快照，遵循相同流程；禁止自动重放已取消队列。

```bash
python3 manage.py acknowledge-restore --db /var/lib/harbor-home/service.sqlite3 --offline-confirmed --review-confirmed
```

## 数据保留与客户删除

Windows合成运维可使用指南中的PowerShell动作；它们实际调用manage.py并共用原生
实例排他和恢复门禁。Windows恢复将源库先校验、准备独立文件、关闭SQLite/WAL句柄，
并保留经核验的回滚/事务证据处理替换失败。若存在待恢复记录，启动及进一步破坏性
操作保持阻断；不要手工删除未知WAL/SHM/事务文件。按已记录的同一合成目标、停止所有
使用者并明确offline后，可在本机使用 `manage.py recover-restore --db ... --offline-confirmed`
核查；它不会把未知文件当作已恢复。恢复独立审查及暂停仍需正常流程，不能靠普通
发送恢复按钮绕过。客户库和凭据不使用本指南可公开的QA密码/HMAC。

停止服务后，在安全加载相同 `PRIVACY_HMAC_KEY` 的运维进程中执行：

```bash
python3 manage.py maintenance --db /var/lib/harbor-home/service.sqlite3 --retention-days 30 --offline-confirmed
python3 manage.py delete-contact --db /var/lib/harbor-home/service.sqlite3 --offline-confirmed
```

删除命令私密提示 E.164 号码，不通过命令行参数或日志传递号码。维护删除超期不活跃客户及其关联内容；仍活跃的客户保留当前联系方式/状态，但删除超期消息、预约请求、事件、同意证据和审计内容；未完成预约状态按其独立的 `booking_updated_at` 清理，避免后来一条 FAQ 延长旧联系信息的保留。没有时间戳的旧版本预约状态在该客户旧消息被删除时保守清理。队列关联内容同步删除，不再参与发送；已提交、已接受或待核实的外部短信无法撤回，结果计数中的 `ambiguous_removed` 表示此类记录被删，之后回调无法再与删除记录关联。删除不会清除无号码/正文的每日分段预留和 API 调用次数账本，因此不能靠删除客户或恢复旧备份重置当日用量限制。

保留稳定 HMAC 的本地/运营商退订抑制及 SHA256 事件/SID 墓碑，以避免删除后旧 webhook 重放或普通咨询重新启用退订号码。墓碑不包含号码、正文、姓名或预约信息，但仍是安全用途的假名化记录，需纳入客户隐私说明；当前不自动到期，以保留去重和退订防护。平台明确恢复订阅后仍受其他发送限制约束，不构成营销授权。

删除使用 SQLite secure_delete、WAL checkpoint 和 VACUUM 清理当前数据库的释放页面；不会冒充操作系统级安全擦除。先审查备份和 provider 数据的独立删除/保留政策。请先在合成数据库演练删除与恢复；不要在运行中的真实客户数据库试验。

真实测试开始前明确清理责任人、日期及保留证据范围。结束后关闭真实外发并暂停
队列，审查 pending/uncertain；按协议删除临时客户内容和测试备份，撤销临时权限/
凭据并关闭测试入口。旧备份/provider 数据/磁盘快照由各自持有人按已批准政策处理，
安全用途的假名化退订/去重/额度记录保留，报告应清楚列出，不能声称全部痕迹消失。

## 故障排查

| 现象 | 处理 |
|---|---|
| 签名错误 | 比较实际 Twilio HTTPS URL、原始 path/query、Auth Token 所属账户、全部表单参数；不关闭验签 |
| 入站 pending / verification_failed | 核查只读资源访问凭据、网络、账户/号码匹配和请求时间；不绕过资源核验 |
| outbox queued 不发送 | 查看暂停、紧急文件、live 开关、授权记录、白名单、消息/分段限额、服务回复时窗 |
| uncertain | 核实平台是否接受；已知 SID 可只读对账；无 SID 不自动重发 |
| 21610 | 保持本地和运营商退订阻断，核对真实号码/退订配置；不要发探测短信 |
| delivered 但参与者未确认 | 将送达回执与手机实际收到分开记录；delivered 不代表已读 |
| 恢复后无法发送 | 这是预期安全状态；核实备份之后的发送与退订，不能直接解除全部限制 |

真实验收应记录脱敏 MessageSid/时间/方向、API 接受、sent、delivered 和参与者手机确认。所有没有真实证据的项目标为“真实联调待完成”。
