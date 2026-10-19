# 其他agent安装与接入

运行环境：Python 3.10或以上，以及匹配系统/架构的预编译运行包。支持Apple Silicon Mac、Linux x86_64和Linux ARM64；不把Mac程序放到Linux运行。

1. 校验交付SHA256，解压选定运行包到固定目录。
2. Codex按[本地安装](CODEX-LOCAL.md)注册；其他支持MCP stdio的宿主，将command设为python3，args设为已解压包的runtime/codex_mcp.py绝对路径，cwd设为包根目录。需要自定义配置时追加--config和配置绝对路径。
3. 将包内skills/xiaohongshu-operations/SKILL.md与skills/humanizer-zh/SKILL.md作为技能入口；保留整个目录，不能只复制两个SKILL.md。宿主必须能读本地文件，并具备图片生成及实际看图能力。
4. 收到私人配置包时运行包内说明的路径转换命令；无配置时按首次引导。当前机器接续使用原状态，不能初始化空库。
5. 准备独立运营浏览器并人工登录。工具不自动接管普通个人Chrome；CDP地址按实际宿主填写。
6. 用initialize、tools/list、get_runtime_status核对接入，制作前读取完整content_profile和原文。没有真实发布任务时不创建日程或执行发送。

运行包包含全部内容方法、视觉模板、许可、Python运行模块与平台程序。源码包用于维护，不是直接运行包。
