# Agent接手入口

先读 [README](README.md) 和 [运行规则](docs/RUNTIME.md)，内容任务进入 [运营skill](skills/xiaohongshu-operations/SKILL.md)。本包不携带任何账号的历史批次或发送授权。

同机接续：使用原私有配置与原状态目录，检查版本、账号、pending_tasks、continuous_operation及output_dir/CURRENT-RUN.json，沿原编号接续，不重新发送成功或未知内容。

换机首次使用：安装对应平台包，导入单独提供的业务配置，人工登录并核验身份。业务配置导入不搬运任务库、cookies和浏览器profile；需要继续原任务时必须迁移最新完整状态及证据，不能以空库重跑。

维护：在唯一源码修改，保留其他任务改动；使用锁定工具链检查、冻结打包，按实际报告核验。升级先用旧入口停止自有服务，保留浏览器与最新发送事实，不以旧备份覆盖。
