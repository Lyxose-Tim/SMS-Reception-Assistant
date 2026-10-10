# 本轮平台支持判定

本表适用现有单商家 Python＋SQLite 项目：一个进程、多个有界 HTTP 线程、一个发送
worker。不支持多进程共享同一个活动数据库发送、多 worker、共享/网络文件系统。

| 环境 | 当前结论 | 依据 / 剩余条件 |
|---|---|---|
| 本机 Windows 11、CPython3.13.9、项目 `.venv`、固定本地NTFS、回环合成数据 | 本报告配置下原生功能验收完成 | 正式PowerShell→app.py/service启动、登录UI、真实LockFileEx子进程排他、异常退出/重启、SQLite备份恢复、维护及完整业务回归；见 [最新独立最终报告](FINAL_ACCEPTANCE_REPORT.md) |
| Windows真实客户数据 | **候选/受阻** | 需另有符合访问边界的私有目录及授权独立用户访问证明；当前项目ACL宽；不改ACL或创建用户 |
| Windows符链权限、现存8.3名称 | 符链部分未验证；C盘既有短名已实测 | 4原子场景实际WinError1314；D盘原fixture无短名仍skip，C盘现存8.3父路径另有真实第二进程拒绝/释放后重获证明；不改开发者模式或8.3设置，不用junction冒充符链 |
| Windows网络盘、UNC、远程/未知reparse目标 | 不支持；相关真实负向场景未验证 | 在目标IO前拒绝非固定本地盘/不可证明目标；没有创建网络共享或改变系统设置 |
| Linux部署选项 | 保留，**本轮未测** | 原flock、0600、权限/fsync断言和部署模板保留；原Linux报告属于历史运行；另需现有获准Linux主机执行完整回归 |
| 实际Twilio API接受、回调与手机收取 | **未执行** | 离线SDK/signature mock不等于真实SMS证据；须 [E2E manifest](REAL_E2E_PLAN.md) 另行授权 |

路径规则允许经验证的现存本地父junction归一至同一物理数据库；拒绝叶reparse、
硬链接、UNC/ADS/设备/歧义路径。经reparse父别名创建缺失尾组件拒绝。
路径检查不能替代目录访问控制或防止有修改权限的另一账户并发篡改。

正常恢复保留原DACL/owner/group原始字节；Windows可能添加描述符继承标志。
真实只读前后对比见报告。当前AccessCheck仅证实当前token，不能认证跨用户私密性。
Windows限制不通过降低安全要求或mock成功消除。没有 production-ready、运营商必达
或注册必过承诺。

最新独立完整运行 382/380/377/3/2 的精确统计及新 GUI 95 断言、桌面实体双击见最终报告。
本机合成配置可以使用，客户数据仍候选/受阻；没有扩大网络盘或跨用户条件支持。
本轮实际 Ctrl+C/Ctrl+Break 未运行，使用本地正常 Stop 请求验证排空/释放。

备份通过 SQLite backup API 在线运行，并持 maintenance SH；正常业务写可并行。
恢复/删除/保留期/恢复复核持 service→maintenance EX，不与备份重叠。
默认 5 秒等待/SQLite 工作预算可选择大于 0、不超过 120 秒；超时不发布、不覆盖。
同步 OS 调用不承诺硬实时可中断，外部工具须停止或遵守同一锁协议。
