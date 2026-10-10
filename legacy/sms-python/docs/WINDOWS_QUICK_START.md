# Windows 快速开始 — 本地合成演示

双击当前桌面的 **SMS Reception Assistant**。健康检查确认本次实例后，浏览器打开
`http://127.0.0.1:8765/`。页面醒目标明 **DEMO / SIMULATED**，联系人、号码和回复均为
虚构；真实发送关闭。不要输入真实客户资料。演示不要求正式服务密码，不代表正式
服务认证或真实短信验收。

当前已安装并实际双击验证的快捷方式：

| 字段 | 本机实际值 |
|---|---|
| 位置 | `C:\Users\Lyxose\Desktop\SMS Reception Assistant.lnk` |
| 目标 | `C:\Users\Lyxose\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe` |
| 参数 | `-NoLogo -NoProfile -WindowStyle Hidden -File "D:\SMS Reception Assistant\scripts\desktop.ps1" -Action Launch` |
| 工作目录 | `D:\SMS Reception Assistant` |
| Python | `D:\SMS Reception Assistant\.venv\Scripts\python.exe` |

安装器通过 Windows Known Folder 解析当前 Desktop，可适配 OneDrive 重定向；本机实际
解析为上述路径，没有对 OneDrive 同步本身作验收。快捷方式不含密码、令牌或真实号码。
空格和中文路径、从其他工作目录启动均有真实子进程测试；超长路径没有承诺普遍支持。

## 使用与退出

再次双击会复用同一受管理实例并打开页面，不再启动一个 app。程序核对 run ID、PID
及 OS 创建时间、项目虚拟环境、数据库、端口和 numeric loopback 健康响应。若端口被
别的程序占用，会明确报错；不会认领或结束该进程。

点击页面底部 **Exit local demo** 正常退出。仅关浏览器标签不会退出服务。每次重启
使用全新的合成库，之前的日志保留在 `runtime/desktop-demo-v1/sessions/<唯一ID>/`；
不打开旧 `runtime/demo.sqlite3`、`runtime/service.sqlite3` 或客户库。验收清理状态
见最终报告，快捷方式与 namespace 保留，下次双击自动创建新的演示会话。

需要手工查看或退出时，在任意目录运行本机已有的 PowerShell 7.5+：

```powershell
$pwshPath = 'C:\Users\Lyxose\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe'
& $pwshPath -NoLogo -NoProfile -File 'D:\SMS Reception Assistant\scripts\desktop.ps1' -Action Status
& $pwshPath -NoLogo -NoProfile -File 'D:\SMS Reception Assistant\scripts\desktop.ps1' -Action Stop
```

Stop 请求 app 自身 shutdown/drain、关闭 SQLite 并释放真实跨进程锁，超时明确报告；
不按进程名或端口强杀。没有开机自启、Windows 服务、计划任务、管理员需求或系统
设置修改。后台演示仅是本轮用户直接批准的本地体验方式。

## 环境检查与修复

启动前检查项目 `.venv`、16 个固定依赖及 Twilio 实际导入。缺少依赖时显示修复指引，
不静默下载/安装、不改全局 Anaconda、PATH、执行策略或 ACL。已有环境可由你明确
选择后运行：

```powershell
& 'D:\SMS Reception Assistant\.venv\Scripts\python.exe' -m pip check
& 'D:\SMS Reception Assistant\.venv\Scripts\python.exe' -m pip install -r 'D:\SMS Reception Assistant\requirements-windows.lock.txt'
```

`.venv` 缺失时另行选择 `scripts/windows.ps1 -Action Setup`，使用已有解释器建立隔离
环境；桌面启动不调用 Setup。重新安装快捷方式使用 `desktop.ps1 -Action Install`。
同名且目标/参数/工作目录不同的快捷方式会拒绝覆盖，须先由用户处理冲突。

本机 Python 的 `Failed to find real location of D:\Anaconda\python.exe` 警告在独立查询
中仍可见；实际解释器路径、venv 隔离和退出码均已核验，`pip check` 通过。不要据此
切换到全局 pip 或静默重装系统。日志和状态收据在各自唯一会话目录。

## 正式服务入口与切换条件

桌面合成 demo 与正式 service 入口分开。正式入口使用项目 `.venv` 的 `app.py`、
`APP_MODE=service`、登录/CSRF 和原生数据库锁；没有 demo 模拟 API，也没有 demo
退出按钮授权绕过。安全合成验收可以使用前台入口：

```powershell
& $pwshPath -NoLogo -NoProfile -File 'D:\SMS Reception Assistant\scripts\windows.ps1' -Action Start -Port 0
```

按其新运行目录 `launcher-ready.json` 的实际 URL 打开并登录。默认密码
`Synthetic Windows password 123!` **仅用于合成验收，绝不能用于客户资料**。
窗口前台等待；另一窗口运行：

```powershell
& $pwshPath -NoLogo -NoProfile -File 'D:\SMS Reception Assistant\scripts\windows.ps1' -Action Stop -RunDirectory '<该次项目内run目录>'
```

脚本只接受独立合成标记库，不是客户部署器。Ctrl+C/Ctrl+Break 的真实控制台信号本轮
未验收；正常 Stop 请求已有真实释放证据。客户运行仍需合格私有目录及跨用户拒绝
证明、安全操作员认证/稳定隐私密钥、客户合格号码/注册、批准路由/发送策略、独立
授权真实 E2E 和运维责任。不要把修改一个 `APP_MODE` 当作已经满足这些条件。

## 测试与运维

`windows.ps1 -Action Test` 使用项目 `.venv` 和全新证据目录，不覆盖历史记录。
完整命令/统计和新增断言见 [最终验收报告](FINAL_ACCEPTANCE_REPORT.md)。

备份使用 SQLite backup API，可与正常业务并行；maintenance 共享锁防止与恢复/清理
冲突。默认等待/SQLite 工作预算 5 秒；直接用项目 Python 的 `manage.py backup
--timeout-seconds` 可选大于 0、最多 120 秒。PowerShell `-TimeoutSeconds` 不改变
该预算。同步 OS 调用不承诺硬实时中断，不能直接复制使用中的 DB 代替备份。
恢复/清理仍停服排他，恢复后默认停发和人工复核。具体命令见
[Windows 指南](WINDOWS_GUIDE.md) 与 [运维及责任](OPERATIONS_ZH.md)。

当前范围 $350 / 10 天 / 1 次范围内修改，单商家、一个合格客户自有 Twilio 号码、
最多五条批准 FAQ、待确认预约、人工接管/回复、退订日志、源码及经授权真实收发测试；
第三方费用客户承担。真实测试包含在范围内但本轮未执行，需 [E2E 计划](REAL_E2E_PLAN.md)
中账号/号码资格、参与者、精确正文/方向/条数/分段、回调、预算/时间、停止和清理的
具体授权。Linux 本轮未跑；不声称 production-ready。
