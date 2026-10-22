# 原版能力与云浏览器兼容性矩阵

本文件是原版契约清单和有证据的迁移验收边界，不是“全部功能已经在云端运行”的声明。检查日期：2026-10-19。原版基线提交：`56fa5b7d1ef849d6623244e63de04af434ab2333`，版本 `0.6.0`。实机证据只覆盖下文明确列出的页面和时间；没有执行外发来测试能力。

## 1. 清点范围和状态含义

原版实际注册 **30 个 MCP 工具**：`mcp_server.go` 21 个、`queue_bridge.go` 7 个、`runtime_tools.go` 2 个。`mcp_server.go` 末尾“Registered 20”日志是旧计数，不能作为能力依据。完整 JSON schema 见 [original-tools.json](../schemas/original-tools.json)，下表逐一概括；将来增删工具仍应以实际 `tools/list` 为准。

- **读**：读取平台内容；其中通知列表有已读副作用，私信打开可能有平台已读副作用，不能把 MCP 的 `ReadOnlyHint` 当作无副作用保证。
- **写**：平台发布、评论、回复、点赞、收藏等。
- **认证**：二维码、短信、登录资料处理；不等同于普通内容读取。
- **本地读／本地写**：任务、批次、配置、提交回执和诊断状态；本地写不会因为没有网页动作就变成只读。
- **页面已验证**：宿主支持的 CUA 工具确实读到了该页面。只证明列明的 UI／内容，不证明原版结构化字段全部可得。
- **入口已验证，动作未测**：看到界面或控件，没有实际上传、填写、提交或验证结果。
- **未测／受限**：没有足够实机证据，或安全的公开 UI 路径不能获得原版字段。
- **本地可迁移**：逻辑不依赖浏览器，但必须完成对应云端实现与测试；不是原 Python/CDP 配置已经能够直接在 `cloud_host` 工作。

宿主 CUA 与原 Go/CDP 是两条不同的执行路径。原 Go 浏览器依赖 `XHS_CDP_URL`／`XHS_CDP_PORT` 和 Rod；无 CDP 不得靠导出 cookie、读取浏览器内部对象、私有接口或另起浏览器绕过。安全云端实现由获准宿主浏览器工具操作真实 UI，只有实际完成的动作才能记执行结果。创建计划、保存 observation、生成步骤和登记工具名都不等于执行。

## 2. 全部 30 个原 MCP 工具

### 当前云端候选的实现范围

当前 `cloud_host.py`／`cloud_mcp.py` 暴露全部 30 个原工具名和输入 schema，并另提供明确的宿主动作协议助手工具。原网页工具调用返回待宿主执行的计划，**不会返回原 Go 的成功形状**；之后由实际宿主 CUA 执行并记录账号、目标、原参数、attempt 和可见结果。工具名／计划兼容不等于原版完整结构化结果兼容，更不等于这些写操作已完成实机验收。

`cloud_batch.py` 复用原 `ContinuousOps`／`ContentReview` 的本地资料、独立阶段文件、哈希审查及排期，并以明确云端语义实现全部 11 个批次子操作：

- configure／begin／update／schedule／review／status 保留原参数结构；prepare_cloud_batch_payload 校验准确已审内容并产生固定本地媒体及 SHA256 manifest，仍不创建或执行网页动作。bind_cloud_batch_action 只关联真实持久宿主动作，必须匹配账号、run_id 和准确内容。
- cancel 仅通过宿主动作账本原子取消尚未 claim 的本地动作；不取消网页预约帖或中断已提交动作。
- finish 检查本批每个实际关联动作。只有 host_reported_success／not_applied／cancelled 才可结案，保存 host_reported_complete 及绑定摘要；未知／执行中／待执行仍保留，绝不转换成独立核验的 Go succeeded。
- accept 保留 human_acceptance_ref，并额外要求 `host_acceptance={run_id,account_id,completion_digest,user_source,user_quote,accepted_at,evidence_ref}` 精确绑定本批结果和真实用户验收来源。记录为 host_accepted，后续批次可沿用本地质量验收状态；该记录不授予无人值守或新外发权限，宿主每次仍核对现有授权。
- repair 保留原描述字段和 verification_report 参数；检查实际当前 runtime 模块哈希、版本及报告中的通过记录。测试命令结果明确为宿主报告，不宣称重新运行 Go/CDP 验收、修改源码或激活生产版本。
- maintain 当前只检查并保留全部批次／固定素材／审计证据；**自动删除与压缩尚未迁移**。不是原版清理完全等价，也没有长期磁盘占用保证。

本地云动作账本另提供精确绑定、授权来源报告、新鲜账号观察、领取、发送前记未知、结果报告、原编号核对、暂停和恢复未发送租约。它不独立认证报告真假、不启动浏览器或后台 CUA worker；原始验证码／登录由宿主正规登录或接管，delete_cookies 明确不适用于云浏览器退出。下表“原运行前提”描述原版，云实现差异以上述明确边界为准。

本次 `test_cloud_batch.py` 的 21 项离线合成测试通过，涵盖阶段／素材变更、排期持久化、账号绑定、只取消未领取动作、未知结果保留、绑定验收、不授予自动外发、真实本地哈希核对、私有路径／替换 symlink 和状态丢失。这些测试从未操作真实平台，也不能代替活体验收。

字段记法：`s` 字符串、`s[]` 字符串数组、`b` 布尔、`i` 整数、`n` 数值、`o` 对象；`?` 可省略。不带 `?` 的字段为 schema 必填。注释中的枚举、互斥和限制还须业务校验，未必由 JSON schema 表达。

共用字段组：

- `M` = `run_id?:s, approval_ref?:s, operation_id?:s`，仅四类内容写工具使用；引用文字本身不产生用户授权。
- `O` = `operation_id?:s`，稳定提交编号。相同编号变更动作参数必须冲突；换编号也不能规避内容去重。
- `P` = `tags?:s[], schedule_at?:s, is_original?:b, visibility?:s, products?:s[], activity?:s`。
- `R` = `fresh?:b, compact?:b, result_limit?:i`，仅搜索和详情完整公开这些读选项。`fresh` 不绕过节奏、预算、验证墙；`result_limit` 只裁剪返回值，不降低实际采集量，0／省略不裁剪，1–100 有界。
- `T` = `tab?:s`，主页取 `note`（默认）、`fav`、`liked`。

| # | 原工具 | 输入 schema 摘要 | 分类、原运行前提 | 安全云端 CUA／迁移判定 |
|---|---|---|---|---|
| 1 | `check_login_status` | `fresh?:b` | 读／身份。检查主站真实用户身份，短缓存；创作平台登录不能替代主站身份。 | 主站本人主页已验证可读。应核对真实账号标识及观察时间；不能把适配器在线或 cookie 存在当作已登录。 |
| 2 | `get_login_qrcode` | 无声明参数，传 `{}` | 认证。`XHS_ALLOW_LOGIN_ACTIONS=1`；返回二维码图和超时，并等待扫码登录。虽然标注只读，实际涉及认证流程。 | 未重新打开二维码或测试扫码。只可使用宿主正规登录／接管机制，不读取或复制登录存储。 |
| 3 | `login_with_sms` | `phone:s, code?:s` | 认证。`XHS_ALLOW_LOGIN_ACTIONS=1`；首次缺 code 触发短信，后续提交 code。 | 未测；发送短信、输入号码和验证动作须有对应授权及受支持登录路径。不能以保存参数伪称登录完成。 |
| 4 | `delete_cookies` | 无声明参数，传 `{}` | 认证／本地写。`XHS_ALLOW_LOGIN_ACTIONS=1`；清磁盘 cookie 备份并使本地身份缓存失效。 | 原 attach 模式下**不登出宿主浏览器**。云端不能宣称删除本地空文件已完成账号退出；宿主会话清除需独立受支持路径与授权。 |
| 5 | `publish_content` | `title:s, content:s, images?:s[], M, P, long_article_template?:s, topic_template?:s` | 写。主站与创作平台账号、写闸、状态绑定、素材、间隔和回执；配置运营桥后需匹配准确已审内容。普通图文至少 1 图，长文模板路径可无图。 | 图文上传入口与文件选择控件已验证；上传、文字／图片编辑、全部设置、提交、结果核验未测。不能据入口可见宣称发布可用。 |
| 6 | `list_feeds` | 无声明参数，传 `{}` | 读。主站会话／浏览器、读节奏和预算；返回首页笔记列表。 | 首页 feed 已渲染。可读取可见卡片及其公开链接，未证明所有原版隐藏状态字段可取。 |
| 7 | `search_feeds` | `keyword:s, filters?:o, R`；筛选字段见下一节 | 读。原版要求主站登录；读／搜索缓存、节奏及每小时预算。 | “面试”搜索实际返回笔记卡片与计数已验证；各筛选和分页未测，不能推定全部可用。 |
| 8 | `get_feed_detail` | `feed_id:s, xsec_token:s, load_all_comments?:b, limit?:i, click_more_replies?:b, reply_limit?:i, scroll_speed?:s, R` | 读。真实笔记和对应访问 token；正文、媒体、作者、互动、评论；默认前 10 条一级评论。 | 已验证真实正文、图片指示、公开评论及嵌套回复摘要可读。展开、全量分页、评论 ID、签名视频／字幕地址未验证或受可见 UI 限制。不可伪造 token 或读取私有状态。 |
| 9 | `user_profile` | `user_id:s, xsec_token:s, T` | 读。真实目标及匹配 token；公开资料、关注／粉丝／获赞数、指定 tab。 | 本人主页可读不是其他用户所有 tab 已验证。对方隐藏收藏／点赞须如实受限；不能绕过隐私设置。 |
| 10 | `post_comment_to_feed` | `feed_id:s, xsec_token:s, content:s, M` | 写。准确目标、真实授权、写闸／账号／节奏／持久回执；运营桥下需准确审稿匹配。 | 已验证评论区和未填写时禁用的发送按钮；未展开输入、填字或发送，不证明可完成评论。 |
| 11 | `reply_comment_in_feed` | `feed_id:s, xsec_token:s, content:s, comment_id?:s, user_id?:s, M` | 写。`comment_id`／`user_id` 至少一项；comment_id 优先，否则原实现匹配该用户第一条评论。 | 已看到公开评论的回复／展开回复入口，未点击回复。须确认具体评论和作者；只知道昵称或第一条匹配不足以保证对象正确。 |
| 12 | `publish_with_video` | `title:s, content:s, video:s, M, P` | 写。单个本地视频文件；不支持直接视频 URL。与图文同一账号、闸和回执规则。 | 视频入口已验证；实际选文件、上传／转码、原创／排期等设置、提交及结果未测。 |
| 13 | `like_feed` | `feed_id:s, xsec_token:s, unlike?:b, O` | 写。false／省略点赞，true 取消点赞；目标已是所需状态时跳过，仍走统一写保护。 | 笔记点赞控件可见已验证，本次未点赞／取消。须读前后状态，不可盲目 toggle。 |
| 14 | `favorite_feed` | `feed_id:s, xsec_token:s, unfavorite?:b, O` | 写。false／省略收藏，true 取消收藏；已是目标状态跳过。 | 笔记收藏控件可见已验证，未收藏／取消；需真实账号、正确笔记及可见前后状态证据。 |
| 15 | `get_my_profile` | `T` | 读。当前登录账号的资料、互动统计及笔记／收藏／点赞 tab。 | 主站本人主页已验证。收藏／点赞 tab 和完整分页仍需验证；不能用创作者昵称替代主站唯一身份。 |
| 16 | `get_unread_count` | 无声明参数，传 `{}` | 读。原版在首页读 `window.__INITIAL_STATE__` 的 mentions／likes／connections／总 unread，避免清已读。 | **字段级受限**：只能报告实际可见未读标记；不能用隐藏状态注入或先打开通知再声称未清已读地取得三分区数。 |
| 17 | `list_notifications` | `tab?:s, limit?:i`；tab=`mentions` 默认／`likes`／`connections`，limit 默认 20 | 读但会清该分区未读标记。返回评论、用户、笔记目标、当前 liked 状态；过滤已删除／不可见项并报告 filtered。 | 本次为保留未读状态未打开通知页。须披露已读副作用；不能声称保留未读，缺失 ID／token 不得补造。 |
| 18 | `reply_notification` | `comment_id:s, content:s, O` | 写。通知页对指定评论回复；账号／写闸／持久回执。不是四类内容审稿桥工具之一。 | 本次未回复；须确认评论目标、原文和可见发送结果，不得仅用 comment_id 推导不可见目标。 |
| 19 | `like_notification` | `comment_id:s, unlike?:b, O` | 写。通知中的评论点赞／取消；已是目标状态跳过。 | 本次未操作；须从真实通知项核对目标和前后状态。 |
| 20 | `list_direct_messages` | `limit?:i`，默认 30 | 读。打开 `/chat` 读取侧栏 name／time／summary／pinned；列表通常无 conversation_id。 | 已验证真实会话列表及摘要可见，未打开任何会话。原工具不是完整消息历史读取；不能把通知当私信。 |
| 21 | `reply_direct_message` | `conversation_id?:s, name?:s, content?:s, open_only?:b, O` | 混合。conversation_id／name 至少一项，ID 优先；open_only=true 只打开、不用写闸；否则 content 非空且统一写保护。 | 未发送。仅打开可能影响已读。必须核对唯一接收者；原名称匹配允许包含关系，云端不能默选歧义对象。原 Enter 成功并非送达证据，见后文。 |
| 22 | `manage_operation_run` | `operation:s, data?:o` | 本地读／写，依 operation。configure／begin／update／cancel／schedule／review／finish／accept／maintain／status／repair。原路径需同机 Python 运营桥。 | 当前 CloudBatch 已实现上述本地／宿主报告语义；验收需绑定报告，维护暂不删除。它不生成内容、不操作浏览器、不创建唤醒计划。 |
| 23 | `submit_task` | `tool:s, payload:o, approval_ref?:s, not_before?:n, dependency?:s, run_id?:s, auto_run?:b` | 本地写＋将来可能平台动作。持久入队即返回；auto_run 默认 true，false 仅保存。原队列仅 12 种工具可入队，见后文。 | 云宿主没有常驻 CUA 执行器时只能等待 agent 合法领取。入队不等于执行；不能声称后台无人值守。 |
| 24 | `get_task_status` | `task_id:s, include_result?:b` | 本地读。任务、阶段、耗时、可选结果；不导航、不重发。 | 当前读取实际云账本，不伪装 Go 状态。必须区分待宿主、执行中、未知及宿主报告结果。 |
| 25 | `reconcile_task` | `task_id:s` | 本地写。读取原编号 Go 持久回执并归账；不导航、不发送、不主动启队。 | 当前读取云动作并给出绑定核对入口；实际核对通过 reconcile_cloud_action，不接受裸文本冒充平台证据。缺失记录不证明未发送。 |
| 26 | `control_task_queue` | `enabled:b` | 本地写。启停新任务领取；暂停不取消已运行动作；原实现唤醒队列 runner。 | 当前持久化控制新领取；不启动 worker，启用不是新业务授权。 |
| 27 | `resume_task` | `task_id:s, evidence:s, approval_ref?:s` | 本地写。仅凭处理证据恢复 blocked；unknown 不允许经此重发。 | 当前返回原动作及绑定恢复要求；旧裸 evidence 不直接改变状态。只可恢复明确未发送的租约；unknown 走实际结果核对。 |
| 28 | `get_runtime_status` | 无参数，传 `{}` | 本地读。队列、执行、身份新鲜度、节奏、限流、资源和 worker；队列桥失败仍返回基础诊断。 | 当前返回云账本／宿主边界；不假装具有原 Go 执行、浏览器连接或平台限流遥测。 |
| 29 | `get_operation_status` | `operation_id:s` | 本地读。原 Go 提交账本，状态和原始结果；不导航、不重发。 | 当前读取账号绑定的云动作与宿主报告，明确不是独立平台验证或原 Go 回执。 |
| 30 | `resolve_operation` | `operation_id:s, status:s, evidence:s` | 本地写。默认禁用；需 `XHS_ALLOW_RECONCILE=1`。仅 unknown→confirmed／not_applied，需实际平台核对。 | 当前不凭旧裸 evidence 改状态；返回绑定核对入口。云核对必须匹配真实原动作／attempt／账号／上下文和可见结果，永不重发。 |

## 3. 读能力的字段与边界

### 搜索筛选

`filters` 的五个字段均为可选字符串：

- `sort_by`：综合／最新／最多点赞／最多评论／最多收藏；默认综合。
- `note_type`：不限／视频／图文；默认不限。
- `publish_time`：不限／一天内／一周内／半年内；默认不限。
- `search_scope`：不限／已看过／未看过／已关注；默认不限。
- `location`：不限／同城／附近；默认不限。位置相关权限不能因有字段而推定已获准。

### 详情及评论

`load_all_comments=false` 默认只读前 10 条一级评论；设 true 才应用 `limit`（默认 20）、`click_more_replies`（默认 false）、`reply_limit`（默认 10，跳过子回复数超阈值的评论）、`scroll_speed`（slow／normal／fast）。名字含 all 不表示无限遍历或完整导出。`result_limit` 是额外的响应裁剪，与实际加载 `limit` 不同。

原版详情能够从页面状态取得图片、视频编码档位直链、字幕地址等签名有时效数据。安全 CUA 能读到可见正文／卡片／评论，并不自动获得这些字段、精确计数或所有 ID。若 UI 不公开某字段，返回明确缺失和原因，不能虚构值、抓取内部应用状态或偷偷调用站点 API 来填满原 schema。读取公开页面可观察链接中的 ID／token，但不代表可以复制认证凭据。

### 原实现不具备的能力

- **没有 follow／unfollow 工具或对应 service／HTTP 动作。** `connections` 是新增关注通知，个人页关注数和搜索“已关注”也都是读取，不是关注操作。
- 没有独立帖子编辑／删除、评论删除、平台定时帖取消／改期、完整私信历史导出、群绑定或禁止复制设置工具。
- `manage_operation_run.cancel` 只取消尚 queued 的本地任务，不撤销已提交到创作中心的预约帖。
- 创作中心出现长文、播客或草稿入口，不表示原 MCP 有播客发布、草稿管理、直播或广告管理能力。

## 4. 图文、视频、长文与发布设置

### 共同发布参数

- `title`：代码按平台计长规则检查不超过 20；`content` 为正文，话题用 `tags` 独立填写，代码也检查实际 UI 长度错误。
- `schedule_at`：RFC3339／带时区 ISO8601，在**实际提交时刻**后至少 1 小时、最多 14 天；省略为立即发布。排期提交仍立即向创作平台产生写动作，仍消耗发布节奏。
- `is_original=true`：必须成功完成原创声明；设置失败应中止。涉及页面声明／协议时仍须遵循实际授权和协议确认要求。
- `visibility`：公开可见（默认）／仅自己可见／仅互关好友可见。显式要求的可见范围不可静默降级；默认公开也须核对实际页面状态。
- `products`：商品名称／ID 关键词数组，须账号开通商品功能。原实现按搜索匹配选首项；云端不能在产品歧义时把“第一个”当作已确认商品，应核对实际商品和授权。
- `activity`：按活动卡片名称包含匹配；有值但找不到入口／匹配卡片则明确失败。文档描述 `activity_name` 别名，但正式 MCP schema 只有 `activity`，兼容性不能承诺不存在的 schema 字段。

### 图文与长文

原直接 Go 图文支持 HTTP(S) 图片或执行主机的本地路径；普通图文至少一图。`long_article_template` 走“写长文→新的创作→一键排版→模板卡片”，不等于 `topic_template`。设置长文模板时 images 可空；找不到指定模板不得换模板或退回普通图文。`topic_template` 走“#话题”旁的使用／管理话题模板并插入原生话题标签；它不是长文排版，视频参数也没有这两个模板字段。

原运营队列另有业务限制：每篇图文最多 5 图；已审批次必须把批准的媒体固定在执行主机的 sealed 副本，不接受可变远程 URL；每个资产最多 64 MiB。平台页面允许的上限与插件运营上限不同，不能据平台容量扩大原业务规则。

### 视频

`video` 为单个本地文件，不接受视频 URL，不能将另一个电脑上的路径当成云浏览器可读路径。必须实际验证宿主文件选择器可读取文件并等待上传／处理完成。运行包虽然内部视频阶段有较长等待，外层工具总超时仍限制执行，不能据 UI 显示 20 GB 就保证任意大视频能在当前调用中完成。

### 宿主上传契约与实测状态

宿主浏览器文档提供 `tab.playwright.waitForEvent('filechooser')`、点击真实上传按钮／输入控件、再 `chooser.setFiles(absolute_paths)` 的路径；不能假定有 `locator.setInputFiles`。文档支持上传不等于本任务已验证文件跨环境可达、上传成功或完整发布。

2026-10-19 的只读 UI 检查看到：

- 10:13:56 UTC，创作中心发布页显示已登录账号，视频、图文、长文、播客入口；视频界面显示 ≤4 小时／20 GB，推荐 mp4／mov。
- 10:14:39 UTC，图文上传页显示上传图片／文字配图；支持 png／jpg／jpeg／webp、单图最大 32 MB、不支持 gif／live，建议比例 3:4–2:1、分辨率至少 720×960；实际 `input[type=file]` 的 accept 为 `.jpg,.jpeg,.png,.webp`，multiple=true。
- 页面存在屏幕外的重复标签。仅过滤 DOM visible 或取第一个匹配不足以保证点中当前控件；须依据新鲜页面证据核对可见几何位置。
- **未选择文件、未上传、未填正文、未操作原创／活动／商品／话题模板／可见范围／排期、未发布。** 以上都不能记为已通过。

## 5. 原版写保护、回执与未知结果

所有平台写工具（包括点赞、收藏、通知和私信）经过 `protectedWrite`：`XHS_ALLOW_WRITE=1`、已初始化且绑定的持久状态、`XHS_EXPECTED_STATE_ID`、主站真实身份等于 `XHS_EXPECTED_USER_ID`、串行互斥、共享节奏和持久提交账本。登录类另有 `XHS_ALLOW_LOGIN_ACTIONS`，不是普通写闸。云端适配也必须保留这些安全性质，而不是只保留函数名字。

四类内容写（一级评论、评论回复、图文、视频）还可经过同机运营桥，准确匹配 run／审稿／参数／素材，内部保存稳定任务号，固定素材，执行并归账；直接调用不会唤醒历史队列。未配置运营桥的原直接 Go 路径仍有写保护，但不能声称进行了内容审稿。其它五类写工具没有同样的内容审稿桥。

原提交账本用 bbolt 按账号与 state_id 绑定，保存编号和内容指纹：

- `in_flight`：发送过程已开始记录；重启时转 `unknown`。
- `succeeded`：原工具回调无错误并保存了返回结果；这是原实现的自动化结论，具体动作的证据质量仍有差异。
- `unknown`：已触发发送点或不能证明未发送的失败／中断。先查记录和实际平台结果，禁止换编号盲发。
- `confirmed`：实际核对后确认已提交；不是自动生成的平台笔记 ID。
- `not_applied`：明确未调用发送，或经过真实核对证明未提交；可在原任务和原授权范围内处理后继续。

同编号不同指纹冲突；同内容换编号也拒绝重复。token、run_id、approval_ref 不能用于绕过去重。本地素材字节变化参与指纹。缺失／损坏账本不允许创建空账本后重发。没有跨平台事务或 exactly-once 保证。

**私信证据缺口必须保留为风险而不能扩大成成功保证：** 原 `dm.go` 在 Enter 后等待约 1.2 秒，置 `sent=true` 并提示“已尝试发送，请以会话气泡核实”；输入框缺失时也可能返回无错误但 `sent=false`。原统一账本以 nil error 记 succeeded，因此历史 DM succeeded 不能单独证明对方收到。云端实现须核对准确会话和新消息气泡／平台明确状态，分清 attempted 与 verified；不能以 Enter 无报错判断送达。

发布回执也没有统一的平台 note/comment ID；只接受真实成功 URL、当前可见成功提示及必要的平台核对，不虚构链接。定时发布确认弹窗不能再次点击主发布按钮。跳首页、登录页、验证页、隐藏旧 toast 或正文出现“发布成功”均不能当回执。

## 6. 队列、恢复和排期

原 `submit_task` 只允许以下 **12 个**工具，不是全部 30 个都可入队：

- 8 个读：`get_runtime_status`, `check_login_status`, `search_feeds`, `list_feeds`, `get_feed_detail`, `user_profile`, `get_my_profile`, `get_operation_status`。
- 4 个写：`post_comment_to_feed`, `reply_comment_in_feed`, `publish_content`, `publish_with_video`。

点赞、收藏、通知、私信和认证工具不在原队列 allowlist。扩充需要显式设计和测试，不能说原来就支持。

`auto_run` 默认 true，只表示纳入原后台队列；false 仅保存。`not_before` 是 Unix 秒的最早执行时间，**与平台 `schedule_at` 不同**：前者约束什么时候开始网页动作，后者是提交给平台的未来发布时间。`dependency` 必须存在，前置任务 succeeded／confirmed 后才运行；已明确失败的前置任务使未执行依赖跳过，独立任务可继续。重复内容返回原任务。

原服务拥有有限后台 runner，按持久下一时刻唤醒同机 Python `advance`；空闲和暂停时只做本地维护，不访问平台；连续 3 次执行器错误暂停 worker，不能靠不断重启解决。云宿主 CUA 未提供常驻执行器时应显式为 agent-mediated，不得将此 runner 描述为可自动调用宿主工具。

恢复边界：

- 暂停队列只停止新领取，不能取消已经发生的发送。
- 过期 running 写租约转 unknown；读任务按有限重试与失败处理。存在 unknown 时，原自动队列停止新的写入。
- `resume_task` 仅恢复 blocked，要求实际处理证据；不能把 unknown 改回 queued。
- `reconcile_task` 本身只查询原回执、归账，不发送。四类直接内容任务核对编号、工具、准确参数／素材指纹、目标、账号和时间；兼容旧手动一级评论导入也有严格绑定。
- 缺失或仍 unknown 的回执保留 unknown。原普通 queued 写若经真实回执核对为 not_applied，可在尝试预算内回 queued，否则 blocked；这并不表示 reconcile 当场重发。启用的原 runner 以后可能领取，因此不能把整条恢复链描述为绝无后续写动作。
- 新直接授权只覆盖独立新目标，不改变原 unknown；仍不得复用旧内容换编号重发。
- 浏览器断连不等于账号登出，也不证明发送未发生。验证码、掉登录、账号错配、平台限制、资源不足都保存进度和证据。

## 7. 批次生命周期的实际子操作

`manage_operation_run` 只有外层 `operation`／`data` schema；内部 data 随操作校验，不能只实现外层接收成功就宣称兼容。

| 子操作 | 实际关键参数和语义 |
|---|---|
| `configure` | `authorization_ref, quality_standard, allowed_tools`，以及可选完整 `content_profile`。必须绑定 expected_user_id；初始 validation_required。扩权限须新授权；资料版本改变使未发送内容审查失效。content_profile 不是逐字段 merge。 |
| `begin` | `plan_ref, plan`；创建带所有权标记的 work／final／sealed 目录；已有活跃批次返回原编号。不会替 agent 做研究、写稿或生图。 |
| `update` | `run_id` 加 plan／plan_ref，或 `content_record={id,stage,artifact}`，或进度 item。source→draft→edited→final 实际文件、哈希与前序绑定；不能事后编造阶段证据。 |
| `cancel` | `run_id, task_id`；只原子取消仍 queued 的本地任务，并释放其未提交预约位置。running／unknown／平台已预约帖不在此范围。 |
| `schedule` | `run_id, count(1..100), first_at(含时区), replace_unsubmitted?`；首时间需在当前 1h–14d 内，后续随机间隔 60–90m，持久保存并复用，跨批至少隔 1h。已绑定 queued／running／unknown／blocked 或成功位置不能被重排静默覆盖。 |
| `review` | `run_id, payloads=[{tool,payload,content_id}], evidence`；绑定真实阶段文件、资料版本、计划、完整最终参数及媒体哈希。发布 schedule_at 必须来自本批 slots；checks 布尔值不能代替审查证据。 |
| `finish` | `run_id, abandon?`；所有任务须终结。允许成功、明确失败和取消一起结案；running／unknown／blocked 保留。首批进入 awaiting_acceptance；返回 outcomes。 |
| `accept` | `run_id, human_acceptance_ref`；必须处于 awaiting_acceptance，并有真实用户验收。原代码检查已启用的“发布类”和“评论类”各至少一次成功，不是证明每个具体工具都已经实测。不能用读取／mock 测试代替完整首批质量验收。 |
| `maintain` | 无需浏览器；仅清理带本批所有权标记且可清理的已完成／放弃目录。保留未知、活跃、待验收证据和小型去重／回执记录。 |
| `status` | 可选 `include_profile:true`；读取当前模式、活跃批次、验收来源和资料完整性，不验证平台或执行内容制作。 |
| `repair` | `failure, change, verification, before_version, after_version, rollback_ref, skill_update, verification_report`；核对冻结版本、实际工具能力、检查退出码和运行模块／程序哈希。只记录验证，不自动修源码／切生产版本。 |

相关能力还包括内容资料、Humanizer／Naturalizer、视觉方法、真人封面授权及来源文件、视频实际观看记录、跨条重复和策略配额检查。这些由 agent＋文件审查工作流实现，不是新增 MCP 平台动作。原程序检查结构、完整性、哈希和配额，不代替内容真实性、质量、画面及授权判断。

原资源／保留默认值不是云端长期稳定证明：运行批次媒体预算 256 MiB、状态预算 512 MiB、最少可用空间 2 GiB／1024 inodes；读详情 24h 后清理，大写入参数／结果 7d 后压缩，明确放弃批次默认 72h 后清理，未知和待验收例外。系统硬配额、24／72h资源曲线、稳定登录和唤醒须另做实际验收。

## 8. 验收结论的最低证据

### 本次平台读取证据摘要

以下是宿主 CUA 实际结果的时间与标签页定位，不是永久可分享链接；没有额外保存截图，不能将标签页号误当长期审计凭证。仅记录能力，不复制用户私信／账号标识：

| 时间（UTC，2026-10-19） | 宿主页面 | 验证内容与未覆盖范围 |
|---|---|---|
| 10:06 | tab 5，本人主页 | 主站登录身份及本人资料可读；此前首页 feed 已渲染。不是所有主页 tab／分页通过。 |
| 10:13:56、10:14:39 | tab 6，创作中心发布页 | 视频／图文上传入口及上述格式约束可见。所有上传／设置／提交未测。 |
| 10:15:17 | tab 7，`/chat` | 现有会话列表与摘要出现，空白详情提示点击消息开启会话。未打开会话、未核验发送编辑器。 |
| 10:15:50 | tab 5，由本人主页点进现有笔记 | 标题、正文、4 图指示（1/4）、标签、日期、零评论状态、评论区域、点赞／收藏控件、禁用发送按钮。未写入。 |
| 10:16:17 | tab 7，搜索“面试” | 实际笔记卡片与计数，不只是页面空壳；筛选和分页未测。 |
| 10:16:34 | tab 7，从搜索点进公开笔记 | 正文、公开评论、嵌套回复摘要、回复及展开 N 条回复入口、评论总数可见；未展开更多、未回复或点赞。 |

通知页没有打开，以免在此次观察中清未读。可见侧栏徽标不等于原工具三分区计数。上述结果也没有证明任何云端自动执行器已经执行对应 MCP 名称。

### 验收要求

1. **契约兼容**：实际 tools/list 的工具名、参数 schema、缺省值和错误边界通过对照测试；30 个名字出现不等于 30 个功能运行。
2. **本地生命周期**：真实临时状态中的领取、暂停、取消、恢复、重启、账号隔离、去重、未知保留、媒体哈希与清理测试通过；不得触碰真实账号的历史回执作测试。
3. **安全 CUA 读取**：逐项保存真实目标、观察时间和可见证据。明确完整字段／分页／筛选尚未验证之处，不能把“页面可开”当完整 API 等价。
4. **平台写入**：仅在真实授权任务中验证，不为能力压测随意外发。逐项核对账号、目标、最终稿、媒体、设置、一次提交、发后回执。未测项继续标未测。
5. **持续运行**：宿主实际支持唤醒、浏览器工具、持久文件和接管。入队＋计划文本没有这些条件时不构成无人值守服务；不能承诺 7×24、永久登录或不触发平台限制。

## 9. 源码证据索引

- [MCP 注册与参数](../mcp/xhs-mcp-custom/mcp_server.go)
- [任务工具及后台 runner](../mcp/xhs-mcp-custom/queue_bridge.go)
- [提交查询／人工解决工具](../mcp/xhs-mcp-custom/runtime_tools.go)
- [平台业务 service](../mcp/xhs-mcp-custom/service.go)
- [统一写入口](../mcp/xhs-mcp-custom/write_guard.go)、[状态／写保护](../mcp/xhs-mcp-custom/runtime_state.go)、[持久账本](../mcp/xhs-mcp-custom/operation_journal.go)、[直接内容桥](../mcp/xhs-mcp-custom/direct_content.go)
- [图文／长文／发布设置](../mcp/xhs-mcp-custom/xiaohongshu/publish.go)、[视频](../mcp/xhs-mcp-custom/xiaohongshu/publish_video.go)、[通知](../mcp/xhs-mcp-custom/xiaohongshu/notification.go)、[私信](../mcp/xhs-mcp-custom/xiaohongshu/dm.go)
- [原 Python 队列](../runtime/xhs_ops.py)、[批次生命周期](../runtime/continuous_ops.py)、[内容审查](../docs/CONTENT-REVIEW.md)、[持续运营字段](../skills/xiaohongshu-operations/references/continuous-operations.md)
- [浏览器 attach 边界](../mcp/xhs-mcp-custom/browser/browser.go)、[原 HTTP 路由](../mcp/xhs-mcp-custom/routes.go)

实机页面证据来自 2026-10-19 宿主 CUA 检查；本文件没有包含账号标识、cookie、短信码、认证 token 或用户私信正文。工具输出中的原始平台结构化字段与安全 CUA 可见数据的差异须由调用方显式处理。
