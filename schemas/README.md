# 插件清单的固定标准规则

`plugin.schema.json` 和 `mcp.schema.json` 来自清单内声明的 Agent Plugins 1.0.0 标准地址；SHA256与来源保存在 `toolchains.lock.json`，测试离线核验原文件哈希。未加入运行时Python依赖。

`TestPortableManifestSchemas`使用已固定的MCP SDK依赖验证本包清单和远程示例，未知字段及无效名称必须拒绝。Go SDK的RE2不支持标准名称规则的负向前瞻；测试精确检查原规则后，将其等价分解为“不得连续--/..”与普通正则，并保留其余Schema规则，不修改标准原文件。

这只验证标准清单格式。标准对 `com.openai` 扩展内容不赋予含义，所以不证明用户账号支持私有导入、OpenAI特定界面字段或云端MCP连接。实际入口和权限仍待Your dot验收。

`content-review.schema.json` 是本项目内容证据输入的契约，独立于上述固定标准清单。它描述私有运营配置、分步注册和实际审查字段；运行时还检查版本、文件哈希、父阶段、最终文案、逐页文字和批次数量。字段齐全不代表语义质量合格，完整操作例子及证据边界见 [内容审查契约](../docs/CONTENT-REVIEW.md)。
