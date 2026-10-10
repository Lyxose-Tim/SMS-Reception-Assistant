# 中文启动与验收说明

沿用现有 Python＋SQLite 与英文后台，不重写项目。默认仍是虚构商家及合成数据的
**Demo / Simulated**。当前 Upwork 范围为 **$350 / 10天 / 1次约定范围内修改**：
单商家、一个合格客户自有 Twilio 号码、最多五条批准 FAQ、待人工确认预约、接管与
人工回复、退订及审计日志、源代码和授权真实收发测试。第三方费用客户承担。
**真实 E2E 仍未执行，不得宣称 production-ready。**

## Windows 开发

进入 `D:\SMS Reception Assistant` 后，可运行：

```powershell
python app.py
```

项目专用环境准备好后，Windows用 `start.bat`（强制demo、外发关闭）或直接调用
`.venv\Scripts\python.exe app.py`。不依赖本机没有的 `py`。打开
`http://127.0.0.1:8765/`；需要时添加 `--port 8766`。
Demo 无需第三方依赖、不发送短信，数据库为 `runtime/demo.sqlite3`。不把真实客户
资料或凭据放入 demo。模拟漏接、模型故障、模拟送达仅用于旧演示，不能当作服务
真实能力。截图开启隐私开关，旧作品不是商业客户案例。

Windows 用于编辑、适用的业务/HTTP/mock 测试及演示。正式服务与完整运维验收路径
继续保留 Linux 选项。Windows专项另提供项目 `.venv`、真实 `app.py` 前台service入口、
跨进程实例锁与合成运维验收，均维持单进程、单worker、本地SQLite。客户资料目录
ACL/私有存储未验收时仍为候选，不把合成启动成功称为客户运行授权。不得安装
WSL/Docker或改变系统/执行策略。新结果与原Linux、原Windows报告分别记录。

推荐 Windows 命令为 PowerShell 7.5+ 下
`& .\scripts\windows.ps1 -Action Setup`，再 `-Action Start -Port 0`；默认仅合成
dry-run，前台等待。正常停止用同一run目录的 `-Action Stop`；回归用 `-Action Test`。
已有库须带合成验收标记，密码/HMAC仅传入当前子进程；不更改全局Anaconda或PATH。
详细参数、真实PID与运维入口见 [WINDOWS_GUIDE.md](WINDOWS_GUIDE.md)。

## Linux 服务准备，保持外发关闭

以下是后续获准的准备说明，不表示本轮已安装、配置真实凭据或部署：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-twilio.txt
.venv/bin/python manage.py password-hash
```

目标 Python 3.12；SDK 版本见依赖声明。密码只在自己的终端私密输入；密码、哈希、
隐私 HMAC 密钥和 Twilio 凭据安全注入，均不得贴到聊天或写入源码。`.env.example`
仅列变量，程序不自动读取它。服务使用独立空库，不把演示记录移入真实服务。

保持 `APP_MODE=service`、`ENABLE_REAL_SEND=false`。运行 `.venv/bin/python app.py`
后登录后台，批准最多五条支持主题的英文 FAQ。未知问题转人工；预约始终待商家
确认，每次新请求重新收集详情；预约规则问题使用 requests FAQ 的批准答案。

人工接管后自动业务回复暂停。回复未知咨询时，选择已核验入站来源，审查正文与
原截止时间，明确确认这是服务咨询且回复仅与它有关；审查只建立人工服务回复
依据，不开放自动回复或营销。STOP、运营商阻断、停发、限额和所有门禁仍生效。
恢复自动化需明确操作，不会自动延长来源截止时间。

STOP 以原子撤销事务 commit 且新事件成功接受为本地边界；此后阻止新业务提交。
已经开始的调用可能先完成，等待中的 STOP 不保证优先取得锁；原始请求到达不代表
退订已成功。等待超时返回可重试失败，不确认成功接收，需 webhook 重试。平台已
接受的短信不能撤回。

后台只读显示收件人策略：默认 `test_allowlist` 仅批准的测试号码；另行明确配置的
`inbound_service` 允许经核验、时限内新客服务咨询回复，不开放任意主动外发。
受控真实测试仍使用白名单策略，不通过切换正式策略增加参与者。详见部署说明。

## 回归与登录后 UI 验收

Windows专项回归用 `& .\scripts\windows.ps1 -Action Test`，写入新的专属run目录。
Linux完整离线回归仍须在获准Linux环境另行执行：

```bash
python -m unittest discover -s tests -v
```

原 Linux 报告的 218 通过与历史独立 Windows **218项：197通过、1失败、20出错、
0跳过** 是两次历史结果，不能冒充本轮新重跑。Windows 平台错误应与业务回归分开，
Linux-only 用例的跳过及原因必须显式显示。`TEST_REPORT.md` 保留上一轮结果；新
Windows专项使用独立报告及逐ID闭环矩阵，原283/211/72与41断言不作本轮通过。
缺少SDK的跳过不算通过。mock不能当真实Twilio收发证据。

完整离线回归之后，在隔离空库、发送关闭、mock 入站下验收登录、五个 FAQ 主题、
未知问题人工审查回复、接管/恢复、多轮与第二次预约、退订取消队列、来源截止、
日志、Outbox、登出及异常提示。已有正常 UI 历史证据保留，但修复后需新验收。

`OPERATIONS_ZH.md` 说明备份、保留期、删除、恢复审查、清理及维护责任。数据库、
备份与服务器环境文件不得打包；新迁移前备份旧库。保留稳定 HMAC 密钥以维持退订
抑制；配置保留期不会自动运行维护。Linux未跑的项不能由Windows通过替代；Windows
平台判定同样必须有实际原生证据。

## 真实测试与交付

按 `REAL_E2E_PLAN.md` 核实客户号码/账户、注册及自定义正文能力、国家/路线、参与者
控制及同意、正文、方向、编码/分段、条数、币种/费用上限、时间窗口和清理责任。
真实收发测试属于当前商业范围，但其外部行动须另外具体批准，当前仍未执行。
账号读取、凭据配置、公网暴露、购买/充值、真实外发、长期部署与发布分别授权。
不向退订号码发探测，不盲目重发不确定提交，不以 START 创建营销许可。

客户或指定维护人负责长期加密备份、保留期调度、旧备份/provider 数据清理、恢复
演练、监控/费用与故障处理；持续维护另定协议。结束测试关闭外发并审查队列/不确定
任务，按约定清理临时记录/备份、撤销临时权限并关闭入口；退订/去重/额度安全记录
保留，不能把在线删除宣传成全部副本擦除。准备说明不表示真实部署/测试已完成。
