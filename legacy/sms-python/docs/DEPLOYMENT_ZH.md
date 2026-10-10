# 部署准备与服务配置（尚未部署）

本文件是客户审批前的配置说明，不是已部署证明。**真实联调待完成。** 本轮没有登录账户、购买、充值、建立公网入口或持续访问。

当前交付范围是 **$350 / 10天 / 1次约定范围内修改**：一个商家、一个合格客户自有
Twilio 号码、最多五条批准 FAQ、待人工确认预约、人工接管与相关回复、退订审计日志、
源代码及授权真实收发测试。真实测试包含在约定范围内，第三方费用由客户承担；具体
外部行动仍需另外授权。Windows专项提供隔离venv、真实前台app入口及原生锁/运维
验收；客户资料目录ACL/私有存储仍待验收，保留候选状态。Linux单实例模板继续保留，
不擅自安装WSL/Docker、系统服务、修改执行策略或ACL。

## 架构与安全边界

单 Linux 进程、Python 3.12、SQLite、现有英文后台；一个商家、一个客户自有的 Twilio 短信号码。进程监听回环，systemd 守护；Nginx 或经审查的等效代理负责 HTTPS。模板位于 `deploy/`，域名、证书和 IP 均为待填写配置。后台默认代理拒绝外部访问；经批准的 VPN/IP 限制与应用登录共同使用。生产中没有模拟输入、重置、拨号、营销或模型端点。

这是Linux部署模板说明，不是本轮已部署证据。Windows合成验收使用
[WINDOWS_GUIDE.md](WINDOWS_GUIDE.md) 的 PowerShell Start → 项目venv → `app.py`
service入口，单worker、多个受限HTTP线程、回环dry-run；正常Stop由app内部drain。
实例记录区分redirector与真实app PID、OS创建时间，不能用fixture加锁代替正向启动。
不安装后台服务或持续配置凭据；真实客户运行必须另审ACL、私有备份及外部接入。

统一发送路径为：签名短信 webhook → 持久化 inbox → 空 TwiML → 只读资源核验 → 规则/人工 → outbox → Twilio REST → 签名状态回调。不能再配置 TwiML 短信回复或另一自动化程序回复同一入站。

## 客户自行安全注入配置

1. 在自己的主机或获准的秘密管理系统创建服务配置，放在项目和 ZIP 外，限制所有者与 `0600` 权限。模板使用 `/etc/harbor-home/reception.env`。不要在聊天交付任何密码、API Key、Auth Token、哈希或隐私密钥。
2. 用 `manage.py password-hash` 私密创建后台密码哈希。生成并稳定保存 `PRIVACY_HMAC_KEY`，同时安全备份；它用于删除后退订抑制，不能随意轮换。默认保留期 30 天需实际运行维护，配置本身不调度删除。
3. 先使用 `APP_MODE=service`、`ENABLE_REAL_SEND=false`。新建空的服务数据库，批准最多 5 条 FAQ，确认人工责任人、预约措辞与隐私规则。不要把演示合成数据移入真实库。
4. 在已授权的本机配置 `TWILIO_ACCOUNT_SID`、`TWILIO_AUTH_TOKEN`、`TWILIO_PHONE_NUMBER`。REST 可另用最小权限 API Key；验签仍需相应 Auth Token。指定 `TWILIO_PUBLIC_BASE_URL=https://实际域名` 与 `ADMIN_ORIGIN=https://后台实际域名`，两者均为 origin，无路径/query。服务器不信任 forwarded header 来重建验签地址。
5. 原始 POST 路径/query 与所有表单参数必须原样转发。状态地址由 outbox 生成，包含秘密关联 nonce；禁止代理/监控日志记录 query、Cookie、Authorization、消息正文或 provider 原始错误。模板关闭访问与错误内容日志，尚需审查客户代理/APM 的独立配置。
6. 公网配置分别为 `POST /webhooks/twilio/sms` 和 `POST /webhooks/twilio/status`。签名错误拒绝；有效签名也需账户、号码、资源和时间核验。默认入站年龄上限 900 秒，极端延迟会保守拒绝，留给人工核查。
7. 默认 `RECIPIENT_POLICY=test_allowlist`，只允许 `TEST_RECIPIENT_ALLOWLIST` 中的
   受控测试参与者。正式入站服务必须另行明确配置 `RECIPIENT_POLICY=inbound_service`
   并记录 `INBOUND_SERVICE_APPROVAL_RECORDED=true`；它只允许经核验、时限内的相关
   服务回复，仍保留退订、运营商、停发、用途与限额门禁，不开放任意主动外发。后台
   只读显示策略，不能开启它。受控真实 E2E 继续使用测试白名单。
8. `TRUSTED_PROXY_CIDRS` 默认为空，登录限流按实际 TCP 对端识别。仅在确认代理
   拓扑且 Python 端口不对外开放后，配置精确可信代理网段；Nginx 必须覆盖客户端的
   `X-Forwarded-For` 为单个实际来访 IP。不盲信任转发头、宽泛网段或任意代理链；
   该信任只用于登录限流来源，不用于重建 webhook 验签 URL。

## 检查账户，不发送

在具备授权且安全配置已加载的客户主机，可运行 `.venv/bin/python manage.py provider-readiness`。它只读取账户及所设号码，输出脱敏能力信息；不会修改号码或退订设置，也不能独立证明注册/合规/自定义消息权限。**本轮没有实际执行这个账户检查。**

需人工核实当前 Console/试用流程、目标国家、号码短信能力与控制权、已批准注册（美国 A2P 10DLC / toll-free 或适用要求）、地区权限、自定义短信支持、真实关键字配置和费用。当前试用文档有预设消息限制，不能假定免费试用可发送本项目自定义 FAQ。

## 启用前的可审阅清单

使用 `REAL_E2E_PLAN.md` 填写号码、参与者控制与同意、逐条正文/方向、编码与分段、币种及金额。发送、购买、公网部署、持续凭据权限分别获准。将同一个授权收件人加入 `TEST_RECIPIENT_ALLOWLIST`；不得临时增加未获授权号码。

`TWILIO_OPT_OUT_PROFILE` 必须匹配真实 `standard` 或 `advanced` 配置。标准 SMS 的 HELP/STOP/START 由平台负责确认，本应用不重复回复；advanced 依赖相应 `OptOutType`。应用不更改账户设置。START/UNSTOP 必须是核验的新事件且已审核 profile，不能靠本地状态假定平台已解禁。21610 持续阻断直至有效恢复。

只有核实且记录后才能将 `TWILIO_OPT_OUT_REVIEWED`、`CLIENT_READINESS_APPROVED` 和
对应策略的授权记录改为 true：受控测试使用 `TEST_SEND_APPROVAL_RECORDED`，正式
入站服务使用 `INBOUND_SERVICE_APPROVAL_RECORDED`。这些开关本身不是证据。最后
单独启用 `ENABLE_REAL_SEND=true`；后台开关状态不证明真实 E2E 或生产验收已完成。

默认应用上限：5 条/分钟、40 条/UTC 日、每条 3 分段、80 分段/UTC 日。GSM-7 扩展及 UTF-16 正确计数；实际运营商/Twilio 编码调整、关键字确认、其他程序流量、租号/注册/税费可能不受此限额约束。预算估算需使用目标路线当前官方费率 × 分段，加运营商与固定费用；不是最终账单硬上限。本轮路线和币种未知，不填虚构金额。

## 上线、故障与回滚

仅允许一个实例持有服务数据库锁；不要增加 gunicorn/多 worker 或多个主机共用 SQLite。服务重启后未提交队列再次检查所有门禁；submitting/不确定超时冻结为 uncertain。只能对已知 SID 只读对账；没有 SID 不盲目重发。入站资源 GET 的有限重试是只读核验，不是 SMS POST 重试。

正式安装模板、`nginx -t`、systemd 重启/硬化、证书及公网验签、实际时延、监控告警、压力/恢复演练均需在获准主机另行验收。本轮只测试代码与本地数据库，不声称模板在部署系统已经通过。紧急开关、备份恢复、清理与回滚完整步骤见 `OPERATIONS_ZH.md`；恢复旧备份后独立核查锁不能通过普通后台解除。

先完成所选平台当前版本的完整适用离线回归、实际实例/运维证据与真实入口登录后
核心UI验收，再评审真实测试清单；未实际跑的Linux项单列，Windows客户资料权限
未验证时不得据此启用真实业务。
Windows 历史独立结果（218项：197通过、1失败、20出错、0跳过）与原 Linux 报告
分别保留，不冒充新重跑；当前实际执行情况以 `TEST_REPORT.md` 为准。客户/指定
维护人负责长期加密备份、清理调度、provider/旧备份副本政策、恢复演练、费用与
故障处理；持续维护另定协议，部署说明不自动安装调度或长期运营服务。

真实验收保存对应的脱敏消息/时间/回调/API 接受/sent/delivered/参与者实际收到记录。sent 或 delivered 不等于已读；没有回调时保留实际最后状态。遇到不确定发送立即停测。退订阻断以零 send API 调用验证，不向退订手机发探测。生产接入仍需客户隐私、安全、目标国家和长期运维审查。
