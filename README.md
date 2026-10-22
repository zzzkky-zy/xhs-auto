# 小红书自动运营插件 · 0.7.0

完整的小红书运营方法、图文制作资源、Humanizer、Naturalizer和配套MCP。研究、评论、回评、图文与视频发布均从 [运营入口](skills/xiaohongshu-operations/SKILL.md) 开始。

## 云端宿主适配

云浏览器只开放 agent 工具、没有公开 CDP 时，使用[宿主协作式云浏览器适配](docs/CLOUD-HOST-BROWSER.md)。本版提供原工具参数的宿主计划、账号与媒体绑定、持久操作账本及本地批次流程；由 agent 的官方浏览器工具实际执行，计划/报告不是平台回执。见[动作协议](docs/CLOUD-HOST-ACTIONS.md)和[30 工具能力矩阵](docs/CLOUD-CAPABILITY-MATRIX.md)。平台写操作尚未实测，不支持无人值守浏览器 RPC；原有本机 CDP 模式不变。

## 0.7.0 更新范围

本版统一纳入发送防重复、旧任务升级保护、私有备份检查、记录修复、日额度边界、通知去重及用户汇报规范。完整范围和未实现部分见[逐项验收](docs/RELIABILITY-ACCEPTANCE-20261022.md)。本地验证不代表已经安装到用户设备或完成真实账号试运行；升级不得自行恢复用户暂停的任务。

## 安装与使用

选择本机平台运行包，先校验SHA256，再按 [安装说明](docs/CODEX-LOCAL.md) 安装。Python只使用标准库，运行包含预编译程序，无需Go或Docker。其他支持MCP的agent可按 [通用接入](docs/AGENT-INSTALL.md) 配置。

收到单独的私人配置包时，按其中说明导入已核对的业务偏好和素材；没有私人配置时执行首次引导。配置、照片、产品资料、账号登录及任务记录不在公共插件包内。

| 用途 | 入口 |
| --- | --- |
| 执行与恢复 | [运行规则](docs/RUNTIME.md)、[MCP字段](docs/MCP-CONTRACT.md) |
| 内容规则与完整方法 | [内容来源](docs/CONTENT-SOURCES.md)、[内容审查](docs/CONTENT-REVIEW.md) |
| 所有画风与制图 | [视觉入口](skills/xiaohongshu-operations/references/visual-production.md) |
| 升级 | [保留记录的升级与回退](docs/UPGRADE-ROLLBACK.md) |
| Linux宿主 | [资源配置](docs/CLOUD-RESOURCES.md)、[验收标准](docs/CLOUD-ACCEPTANCE.md) |

源码包另含构建源码、测试和锁定依赖。修改在源码完成，不编辑安装缓存。发布数量、时间和审批以实际用户授权为准。
