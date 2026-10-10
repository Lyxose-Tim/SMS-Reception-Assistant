# Windows 原生环境对齐专项计划

## 1. 当前状态、核验依据与目标

**原生 Plan 模式已确认；本计划完成后，实施状态仍为未开始。** 用户已预授权专项内的代码、文档、项目依赖安装和安全测试；进入执行模式后沿用该授权，不重复询问同一批准。

本轮已完成只读核查、测试收集及依赖解析 dry-run，未修改源码、安装依赖或重新执行测试。

| 核验项 | 实际结论 |
|---|---|
| 项目与规则 | 没有 `.git`、`AGENTS.md`、`.agents/skills`；不能声称检查过 git diff。沿用已读取的 brainstorming 设计流程。 |
| 外部基线 | `artifacts/windows-alignment-evidence` 中 62 个基线文件及当前对应文件 SHA256 一致，差异为 0；保留该目录及用户更正文件。 |
| 当前解释器 | `D:\Anaconda\python.exe`，Python 3.13.9、AMD64／64 位，`prefix == base_prefix`，不是虚拟环境。启动有 real-location 警告，查询退出码为 0。 |
| SQLite | Python `sqlite3.sqlite_version` 为 **3.51.0**；另独立查询 `D:\Anaconda\Library\bin\sqlite3.exe`，其版本也是 3.51.0。两者分别记录。 |
| PowerShell／入口 | PowerShell 7.6.5；现有策略 RemoteSigned，未修改。`py` 未找到，当前 `start.bat` 因依赖 `py -3` 不可靠。 |
| 依赖 | `.venv` 不存在，Twilio 不可导入。官方 9.11.2 元数据列出 Python 3.13、未撤回通用 wheel；目前无升级理由。[官方版本元数据](https://pypi.org/pypi/twilio/9.11.2/json) |
| 安装预检 | 官方 PyPI dry-run 因沙箱临时目录权限失败，退出码 1；尚未证明完整依赖树可安装，未安装任何包。 |
| 测试证据 | 现有日志与 JSON 对应 **283 项／211 通过／0 失败／0 错误／72 跳过**。本轮仅收集，仍为 283 个 ID，72 个跳过 ID 唯一且全部存在。 |
| 时间更正 | 原 JSON 为 `+00:00`；`-07:00` 是 PowerShell 显示转换。保留原证据，不修复不存在的源文件错误。 |
| 旧 UI | 四阶段结果为 18＋3＋13＋7＝41，截图和清理记录存在；仅作为历史证据，不计作本轮通过。 |
| 存储与权限 | D 盘为本地 NTFS；项目 ACL 允许 Authenticated Users 修改、Users 读取。按用户选择，本轮先完成合成验收，客户数据运行保持候选状态。 |

目标是在现有 Python＋SQLite 项目上增加可靠的 Windows 原生路径，继续维护 Linux 选项；不重写项目、不重复 ZIP 迁移、不恢复旧源码。

## 2. 按优先级实施与文件范围

### P0：保护证据、用户修改和测试身份

- 保持外部 evidence、旧报告、旧日志和截图不变。新证据使用独立、排他创建的 `artifacts/windows-alignment-runs/<UTC时间>-<唯一ID>/`。
- 首次修改前保存实际文件清单、SHA256 和本轮修改范围内的源码副本。补充现有基线未覆盖的配置模板、脚本等；不复制真实凭据、客户数据库或备份。
- 每次修改前核对当前文件与本轮前像；发现用户新修改时读取并合并，禁止整文件恢复基线。
- 用统一源码差异和前后哈希记录改动。保留 72 个原跳过 ID，不通过改名、删除、放宽断言或新增 mock 冒充闭环。

**范围：** 测试运行器、证据记录脚本、新 Windows 专项计划与矩阵文档。现有固定覆盖 `current.*` 和 UI 输出目录的入口必须先参数化，再运行。

### P1：项目 `.venv` 与 PowerShell 入口

- 使用已存在的 `D:\Anaconda\python.exe` 创建项目 `.venv`，不继承系统 site-packages。所有后续命令直接调用 `.venv\Scripts\python.exe`，不依赖激活、`py` 或全局 `pip`。
- 保留 `twilio==9.11.2`。仅从官方 PyPI 安装声明依赖及其必要依赖，优先使用兼容 wheel；不安装编译工具或系统软件。
- 在子进程中指定本轮专用临时目录，避免当前沙箱 TEMP 问题。禁止自动回退到用户级／全局安装。
- 安装后核验实际解释器、架构、虚拟环境隔离、SSL、导入来源、安装版本、依赖树及 `pip check`；记录安装命令、退出码、完整日志和可复现依赖清单。元数据兼容不代替实际测试。
- 新增统一 PowerShell 入口，覆盖 Setup、Start、Stop、Test、Backup、Restore、Maintenance；`start.bat` 转发至明确的项目解释器或提示先 Setup，不盲用 `py`。
- 默认回环、dry-run、真实发送关闭。合成启动和测试清除继承的真实 provider 配置，只使用进程级虚构测试值。首轮前台运行，不创建后台服务或计划任务。
- 不修改 PATH、Anaconda、执行策略或其他环境；策略或工具拒绝时记录阻塞，不使用 Bypass。

**主要范围：** `D:\SMS Reception Assistant\requirements-twilio.txt`、`D:\SMS Reception Assistant\start.bat`、新增 `D:\SMS Reception Assistant\scripts\windows.ps1`，以及运行器和启动说明。

**验收：** `.venv` 隔离成立、版本与依赖检查通过；原 45 项 SDK 测试全部以原 ID、原断言实际执行。只使用既有本地 mock／合成数据，禁止真实 Twilio 网络调用。

### P1：Windows 跨进程单实例与生命周期

- 保留共同接口 `service_lock(db_path) -> canonical Path`：Linux 使用原 `flock`，Windows 使用标准库 `ctypes` 封装原生 `LockFileEx`。
- Windows 同步打开规范化 sidecar，允许读写共享、禁止删除共享；对一个字节使用独占、立即失败的内核锁。句柄不可继承，正常路径显式释放；异常退出后有界等待 OS 释放。锁文件存在本身不是锁。[Microsoft LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)
- 支持本地 NTFS。规范化现有父目录、大小写和已有 8.3 名称；父 junction 别名必须落到同一物理目标。已有文件以句柄最终路径、卷及文件身份复核。
- 数据库、锁和操作目标叶子拒绝 reparse、硬链接、非普通文件；拒绝 UNC、ADS、设备名及无法证明安全的路径。恢复会更换数据库文件身份，因此不能按数据库 inode／file ID 固定锁名。
- 在 Engine 初始化或写库前取得锁；服务、恢复、维护、删除和其他破坏性写入共用该边界。在线一致备份继续是只读复制，不引入第二套写锁。
- 初始化与全部资源进入统一清理生命周期：配置、数据库、端口绑定、worker 启动失败都释放已创建资源；正在运行的线程未排空时不得关闭数据库或提前解锁。
- 前台正常停止支持 Ctrl+C；程序化停止通过本次 run manifest／run ID 绑定的请求文件触发应用自身 shutdown 和 drain。Windows Ctrl+Break 在具备真实控制台条件时单独验证。`terminate()` 只用于异常退出测试。
- 保持一个服务进程、多个 HTTP 线程、一个发送 worker，不扩展多 worker。

**主要范围：** 新增 `D:\SMS Reception Assistant\reception\platform_fs.py`，以及现有 operations、app、manage 和进程锁测试。

**验收：** 真正的 Windows 子进程证明首实例启动、第二实例拒绝、正常停止释放、异常退出后重启、初始化失败释放、别名不能绕过、维护与服务互斥、持锁时 sidecar 不能被改名或删除。仅管理测试创建并记录的进程句柄／PID，不按名称或端口批量终止进程。

### P1：Windows 备份、恢复及维护

- 继续使用真实 SQLite backup API，一致复制已提交 WAL 内容。临时文件同父目录、同卷；完成完整性／外键校验并关闭所有连接、游标后发布。
- Windows 备份采用不覆盖的原生移动及文件 flush；目标已存在或发生竞争时拒绝，保留已有目标。Linux 保留 0600 和目录 fsync。
- 恢复须停写、取得共同排他锁、检查外部 writer，保存独立且已验证的当前库 rollback 快照。
- 在恢复副本中合并 STOP、carrier、墓碑、去重及用量账本，取消／冻结任务，并写入暂停和人工复核门禁。
- 发布前成功 checkpoint、检查 busy 和 journal 状态、关闭全部句柄。非空或无法确认的 WAL／journal 不得盲删。
- Windows 使用带原文件备份名的 `ReplaceFileW`，保留原 DACL，不忽略 ACL 错误。该 API 失败可能已移动原文件，不能把返回失败当作原路径未改变。[Microsoft ReplaceFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)
- 替换前保存恢复 marker。按路径、身份、哈希和 SQLite 校验判断发布状态；确定安全时无覆盖恢复原文件。未知目标、回滚失败或证据不一致时保留原库／rollback／marker，拒绝启动。
- 增加显式 `recover_restore()` 和 `manage.py recover-restore --offline-confirmed`，只处理可证明的恢复状态，不覆盖未知文件。
- 删除和保留期先验证绝对路径及授权边界，再执行真实事务、checkpoint／压缩。压缩失败不能宣称清理全部完成；不递归清理未授权目录。

**范围：** 共用平台适配层、operations、manage，以及运维和 outbox 集成测试。

**验收：** 实际 Windows 文件操作证明 WAL 备份、不覆盖、无效备份拒绝、真实文件占用／sharing violation、替换失败保留原库、恢复重启、marker 阻断和维护互斥。修正测试中未关闭的 SQLite 句柄，保留原业务断言。

## 3. 业务、UI 与平台判定

- 回归 STOP 原子性与提交顺序、未知咨询人工授权、正式入站门禁、五 FAQ、独立预约草稿、待人工确认、接管、退订审计、登录／CSRF／可信代理／限流、kill switch 和恢复门禁。
- 新 UI 验收使用独立合成商家及数据库、实际原生锁、隔离浏览器和禁止发送的 provider。不得复用用户业务数据或旧 41 个通过结果。
- 从首次导航记录路由、交互、断言、网络请求及 JavaScript 错误；401／403 等预期拒绝和 favicon 请求分别归类。全过程检查隐私遮挡，保存新截图及真实清理结果。
- Windows 权限用真实 DACL、安全描述符及访问检查验证；当前身份能读写不等于其他用户被拒。不得用 `chmod` 成功证明 Windows 私密性。[Python chmod 文档](https://docs.python.org/3.13/library/os.html#os.chmod)
- 不修改 ACL、启用开发者模式或创建用户。缺少符链／跨用户访问测试权限时，只阻断相应子场景，仍执行硬链接、路径、数据库和互斥主场景。
- 当前选择为合成验收。即使 Windows 功能、互斥、运维和 UI 通过，客户数据运行仍为**候选**，直到另有满足访问边界的私有目录及必要证明。
- Linux 独有断言保留，本轮未执行；文档只称“保留 Linux 选项”，不声称本轮重新验证 Linux。

## 4. 72 个跳过项的初始闭环矩阵

以下完整 ID 由表中前缀与方法名拼接；正式 JSON 使用完整 `test.id()`。**全部原状态为 skipped，本轮新执行结果均为 `not_run`。**

每行正式记录：原 ID／原原因／源码位置／分类／修复或保留理由／适用平台／Windows 结果／Linux 分支状态／运行 ID／退出码／日志及用例证据位置。旧原因逐条原样复制，不重新归因覆盖原记录。

处置：

- **D：45 项缺 SDK。** 安装隔离依赖后执行原测试；适用 Windows／Linux，原断言和既有 mock 保留。
- **P：23 项当前实现限定 Linux、行为可移植。** 实现后执行真实 Windows 路径，解除整项平台跳过。
- **M：4 项混合平台或权限场景。** 执行共享断言；Linux 专属断言保留，Windows 受限子场景分别记录，不能将部分验证称为全部闭环。

| 前缀 | 完整类名 | 原原因 |
|---|---|---|
| H | `test_http.LocalHttpTests` | `Install requirements-twilio.txt for signed HTTP webhook tests` |
| T | `test_provider.OfficialSDKTransportTests` | `Install requirements-twilio.txt for official SDK transport tests` |
| E | `test_provider.WebhookEnvelopeTests` | `Install requirements-twilio.txt for official SDK signature tests` |
| S | `test_service_http.ServiceHttpTests` | `Install requirements-twilio.txt for signed service HTTP tests` |
| A | `test_twilio_adapter.SignedTwilioIntakeTests` | 与 E 相同 |
| O | `test_operations.OperationsTests` | `Supported Linux-only service lock/backup/maintenance regression; not a Windows business result` |
| B | `test_outbox.ProductionOutboxTests` | `Linux/POSIX offline lock acceptance; not a Windows business result` |
| L | `test_process_lock.ProcessLockTests` | `Linux-only process locking; not a Windows business regression` |

| ID：前缀＋方法 | 处置 |
|---|---|
| H.test_answered_dial_does_not_trigger_missed_followup_but_no_answer_does | D |
| H.test_incorrect_provider_signature_rejected_without_state_change | D |
| H.test_provider_advanced_optout_stale_stop_and_start_preserve_other_gates | D |
| H.test_valid_provider_start_never_creates_enrollment_and_call_alone_is_not_consent | D |
| H.test_valid_signed_webhook_is_empty_twiml_and_replay_does_not_duplicate_reply | D |
| T.test_429_is_only_rejection_marked_safe_for_capped_retry | D |
| T.test_definite_4xx_error21610_is_rejected_and_sanitized | D |
| T.test_optional_api_key_is_scoped_to_configured_account | D |
| T.test_provider_5xx_is_uncertain_without_automatic_second_submission | D |
| T.test_real_sdk_client_has_timeout_no_auto_retries_and_disabled_logging | D |
| E.test_all_unknown_form_parameters_and_raw_query_are_signed | D |
| E.test_duplicate_critical_fields_reject_even_when_all_values_are_signed | D |
| E.test_genuine_status_subset_without_to_retains_signed_row_correlation | D |
| E.test_invalid_or_missing_signature_rejected | D |
| E.test_missing_canonical_configuration_fails_closed | D |
| E.test_multiple_unknown_parameter_values_are_covered_without_loss | D |
| E.test_opt_out_failure_callback_code21610_is_preserved | D |
| E.test_signed_wrong_account_business_number_or_malformed_sid_rejected | D |
| E.test_status_callback_direction_and_exact_query_correlation | D |
| E.test_status_validation_checks_sender_account_recipient_and_sms_status | D |
| E.test_text_only_body_limit_and_supported_opt_out_types | D |
| E.test_valid_envelope_does_not_perform_rest_lookup | D |
| S.test_callback_validates_signature_token_recipient_and_preserves_terminal_status | D |
| S.test_canonical_raw_query_and_every_form_parameter_are_signed | D |
| S.test_http_access_logs_do_not_disclose_phone_body_query_token_or_credentials | D |
| S.test_signed_stop_restricts_queued_business_reply_immediately_without_network | D |
| S.test_signed_webhook_returns_empty_twiml_and_defers_all_provider_work | D |
| S.test_wrong_account_recipient_unsigned_or_json_webhooks_are_rejected | D |
| A.test_advanced_optout_does_not_generate_duplicate_application_confirmation | D |
| A.test_dial_action_result_retains_no_answer_and_completed_for_domain_decision | D |
| A.test_future_resource_is_denied_even_for_stop | D |
| A.test_lookup_unavailable_or_unverified_fails_closed | D |
| A.test_missing_or_malformed_child_sid_is_rejected_before_any_trusted_lookup | D |
| A.test_modified_body_invalidates_signature_before_lookup | D |
| A.test_ordinary_call_completion_without_dial_result_is_rejected | D |
| A.test_parent_child_or_status_proof_mismatch_is_rejected | D |
| A.test_proof_must_bind_inbound_direction_and_exact_message_body | D |
| A.test_signature_covers_all_form_parameters | D |
| A.test_signature_uses_canonical_url_and_raw_query | D |
| A.test_signed_form_without_trusted_freshness_lookup_fails_closed | D |
| A.test_stale_start_is_denied_and_stale_stop_is_enforced_without_confirmation | D |
| A.test_unsigned_or_naive_timestamp_is_not_freshness_evidence | D |
| A.test_valid_signature_creates_verified_inbound_event | D |
| A.test_validly_signed_wrong_account_or_recipient_is_denied | D |
| A.test_verified_proof_must_bind_sender_account_recipient_and_resource | D |
| O.test_backup_captures_committed_wal_and_verifies_permissions | M：WAL／一致性实际执行；0600 仅 Linux，Windows 权限另证 |
| O.test_backup_never_clobbers_existing_file | P：原生无覆盖发布 |
| O.test_backup_rejects_symlinks_and_corrupt_foreign_keys | M：外键拒绝必须执行；符链能力单列 |
| O.test_delete_cli_does_not_accept_or_print_phone_parameter | P：保留私密输入／输出断言 |
| O.test_delete_removes_content_and_queue_but_keeps_stop_and_dedup | P：真实删除及重开 |
| O.test_delete_unprocessed_inbox_without_contact | P：真实删除及墓碑 |
| O.test_invalid_backup_does_not_modify_target | P：损坏源拒绝、原目标保留 |
| O.test_privacy_commands_fail_closed_without_key_offline_or_lock | P：参数拒绝及真实锁冲突 |
| O.test_restore_acknowledgment_requires_explicit_review_and_leaves_paused | P：复核后仍暂停 |
| O.test_restore_keeps_anonymous_spend_and_attempt_ledgers | P：账本不倒退 |
| O.test_restore_keeps_latest_stop_time_and_rejects_start_between_stops | P：最新 STOP 屏障 |
| O.test_restore_preserves_later_stop_and_replay_hashes_without_current_pii | P：抑制／墓碑及隐私 |
| O.test_restore_preserves_rollback_and_freezes_all_pending_work | P：rollback 与冻结门禁 |
| O.test_restore_refuses_external_database_writer | P：实际 writer／占用拒绝 |
| O.test_restore_rejects_malformed_safety_metadata | P：准备失败不发布 |
| O.test_restore_requires_offline_confirmation_and_refuses_service_lock | P：确认及真实排他 |
| O.test_retention_clears_old_unfinished_booking_for_still_active_customer | P：旧草稿清理 |
| O.test_retention_purges_old_inactive_and_active_content_preserving_tombstones | P：真实保留期及墓碑 |
| O.test_retention_removing_inquiry_source_withdraws_current_queue_permission | P：来源删除撤销权限 |
| B.test_actual_offline_delete_reopen_keeps_stop_and_provider_replay_blocked | P：实际删除、重开及重放 |
| B.test_actual_retention_reopen_preserves_carrier_suppression | P：实际保留期及 carrier 抑制 |
| B.test_same_day_privacy_delete_cannot_reset_message_or_segment_quota | P：日账本保留 |
| B.test_same_day_retention_cannot_reset_minute_attempt_quota | P：分钟账本保留 |
| L.test_active_service_lock_refuses_restore_and_retention | P：真实 Windows 排他 |
| L.test_database_and_lock_symlinks_and_database_hardlinks_are_rejected | M：硬链接必须执行；符链权限单列 |
| L.test_path_aliases_share_one_lock_and_yield_canonical_database | M：相对／大小写别名必须执行；原目录符链子场景单列 |
| L.test_second_process_cannot_start_service_on_owned_database | P：真实第二进程拒绝 |

新增用例补齐正常／异常退出、初始化失败、端口占用、8.3／junction、锁文件不可替换、真实 sharing violation、恢复 marker、替换失败、句柄关闭及 Windows 访问边界。

另有一个**原来已通过、并非这 72 项之一**的 `test_windows_operations_fail_explicitly_without_linux_lock` 与新目标冲突。将其明确改为不支持平台／缺少原生能力时的拒绝覆盖，并记录旧新 ID 和语义映射；不能继续拿“Windows 必须拒绝”当支持证据。

统计规则：

- 分别记录收集 ID、实际执行、通过、失败、错误、整项 skip 和受限子场景；部分验证不混算完整通过。
- 输出每个新增、移除及语义变化的 ID；283 是本轮起点，不强行维持未来收集数。
- 原 45 项 SDK 出现缺失或依赖 skip 即未闭环；原 27 项按 Windows 适用断言核验，Linux／能力受限部分保留明确状态。
- 重复运行分别归档，最终报告指向一个选定完整运行，不累加次数制造数量。

## 5. 交付、批准边界与剩余条件

进入执行模式后，在独立新目录保存：

- 本原生 Plan 原文、模式状态、范围审阅及用户预授权引用；实施与工具审批状态如实记录，不改外部 `SUBMISSION.json` 的历史字段。
- Windows 专项计划、实施前后清单／哈希、源码差异和修改摘要。
- 72 项完整 ID 矩阵的 Markdown／JSON，以及每项结果、命令、环境、时间、退出码和日志。
- 新 Windows 完整离线回归、真实进程／运维证据、新登录后 UI 结果与截图、实际清理记录。
- 更新 README、依赖说明、启动／停止／备份恢复说明、平台矩阵、TEST_REPORT、IMPLEMENTATION_REVIEW 和交付说明，保留历史归属。

商业范围保持 **$350／10天／1次修改、单商家、单个合格客户 Twilio 号码、最多五 FAQ、预约待确认、人工接管与回复、退订日志、源码及经授权真实收发测试**，第三方费用另付。

当前明确剩余条件是：

1. 原生 Plan 模式结束后才能写代码、创建 `.venv`、安装或执行专项验收；现有预授权继续有效。
2. 依赖安装必须解决进程级临时目录权限并通过工具审批，失败不得回退污染其他环境。
3. 客户数据私有目录、跨用户访问证明及受限符链子场景未具备；不改 ACL 或系统设置，保持候选／阻塞标注。
4. Linux 本轮未验证；真实 E2E 仍待账号／号码资格、参与者、精确正文与方向／条数／分段、回调环境、预算／时间、停止及清理条件的另行授权。

不安装 WSL／Docker或系统软件，不改防火墙、网络、安全策略、ACL，不创建 Windows 后台服务／计划任务，不配置持续真实凭据，不注册、购买、公开部署或真实收发。工具拒绝须记录动作及原因，其他授权工作继续；不绕过拒绝，不宣称 production-ready。

