# Codex 本地安装与使用

同一入口也支持匹配的Linux运行包；Debian目标的资源配置和接续要求见[CLOUD-RESOURCES](CLOUD-RESOURCES.md)，按实际安装位置定位包，不使用维护机的Mac绝对路径。

工程接入、内容样稿和长期运行分别按实际证据验收。完整内容方法与阶段稿随本轮一起核对；本地检查不能代替实际作品或云端连续运行。

## 安装运行包

使用匹配本机的运行包及同名 `.sha256`。从可信交付报告比对 SHA256，再执行包内 `scripts/setup_codex.py <安装包> --sha256 <确认的哈希>`。脚本核对归档及所有文件，拒绝路径越界、链接、重复文件与清单外文件，用本机 `codex plugin marketplace add` 和 `codex plugin add` 注册安装。已有同版本但内容不同的来源会拒绝覆盖。升级已有配置可附 --config <私有配置>，只更新程序路径；运行中的旧后台须先由旧入口正常停止，账号/状态/写开关保留。

默认来源目录是 `${XDG_DATA_HOME:-~/.local/share}/xhs-operations/marketplace`，可用 `--catalog-root` 更改。目录里只有运行包和本包的本地目录清单；不要放私有配置、profile、cookies 或任务状态。包内含运营入口 `xiaohongshu-operations` 和文字润色 `humanizer-zh` 两个 skill；不要再单独复制到全局 skills 目录造成重复入口。当前 Codex 会话的工具清单可能不热更新，新会话中检查插件及工具可见性。

## 配置与启动

配置单独放 `${XDG_CONFIG_HOME:-~/.config}/xhs-operations/xhs.local.json`；也可由调用者明确指定任意路径。从运行包执行 `python3 runtime/onboarding.py`，自动创建包外私有配置、状态/作品/长期素材目录；优先读取XHS_OPS_CONFIG或默认配置位置，已有文件原样复用，绝不重建或覆盖初始化过的账号。它只准备本地文件，不启动浏览器、创建任务、开启发送或绑定新状态。将 `mcp_binary` 指向安装来源中的 `bin/xhs-mcp-custom`，`state_dir` 与 `output_dir` 指向插件目录之外的私有持久目录。工具返回timezone为空时，由agent依据当前宿主补齐实际时区后再排期，不能默认套用某个地区。保持 `allow_write=false`，账号身份尚未核验时保留 `expected_user_id` 为空；这不能作为真实写入就绪。

在运行包目录执行下列命令，`<配置>` 是私有配置的绝对路径：

```sh
python3 runtime/xhs_ops.py --config <配置> init-state
python3 runtime/xhs_ops.py --config <配置> service start
python3 runtime/xhs_ops.py --config <配置> health
python3 runtime/xhs_ops.py --config <配置> status
python3 runtime/xhs_ops.py --config <配置> service stop
```

先 init-state 绑定，仅首次创建；原状态丢失必须恢复备份。后台默认地址是 `http://127.0.0.1:18061/mcp`。插件使用 stdio，后台地址从私有配置读取；修改 listen 和 mcp_url 后重新启动后台即可，无需改 mcp.json。XHS_OPS_CONFIG 可指定不同私有配置；首次初始化仍须保留明确配置和状态绑定。

Codex 拉起 runtime/codex_mcp.py，每次工具转接先确保属于该配置的后台就绪；无需预先手动开着HTTP服务。后台退出后下一次调用可重新启动，发送后丢回包不会自动重发。任务前使用只读诊断确认身份、版本和原任务状态；无需为了搜索/读帖创建任务或批次。MCP 不自动启动或重启浏览器；宿主提供已有 CDP，端口不可达时显示不可用。安装流程不复制正在用的浏览器资料，不重登、不发验证码、不换设备尝试。登录需用户在执行浏览器私密接管。

## 验收与卸载

`codex plugin list --marketplace xhs-operations-local --json` 可核对已安装状态及加载位置。再核对完整引用和 MCP 握手、tools/list、只读 get_runtime_status，并可提交一条同名本地诊断任务核对后台执行与进度（不访问小红书）。它们证明工程接入；内容需看实际阶段稿和成图，平台发送需真实回执，长期保持需目标宿主24/72小时记录，不能互相代替。

停止服务后可用 `codex plugin remove xiaohongshu-operations@xhs-operations-local`、`codex plugin marketplace remove xhs-operations-local` 移除注册。先查看相应 `--help`，保留私有配置、任务、素材和备份；卸载不清登录状态。升级换固定版本来源，核对 BUILD-INFO 与清单；回滚见 [UPGRADE-ROLLBACK.md](UPGRADE-ROLLBACK.md)，不能用旧账本覆盖新的提交事实。

此流程采用 [官方本地插件布局](https://developers.openai.com/plugins/build/plugins) 及本机 Codex CLI 实际帮助；不是公开插件发布，也不依赖安装钩子或云端常驻假设。

当前任务、配置生效和精简读取规则见[运行说明](RUNTIME.md)。历史手工任务默认不纳入后台，不因升级启用外发。普通动作按需加载、长任务才排队，工程与完整内容资源统一维护，源码位于独立社媒项目；安装后核对实际版本、来源提交及完整内容资源，私有状态不重建。

运行包保留全部运营与视觉资源、许可和必要安装/宿主脚本，不含Go源码、vendor构建代码或测试。需要自动修复/重建时，使用BUILD-INFO中source_archive指向的同版本源码包，在独立源码目录修改并形成新版本；不要在运行包或安装缓存里追加工具链。
