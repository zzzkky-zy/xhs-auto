# MCP 工具契约 · 0.5.0

工具schema以实际tools/list为准，验收检查必需能力与版本，不固定工具总数。get_runtime_status提供Go执行、身份、节奏、限流及资源状态；任务模块不可用时仍返回基础状态并明确高级诊断错误。它不导航。counts是当前保留任务数，不是生命周期外发总量。

运输为 Streamable HTTP `/mcp`，标准 initialize → notifications/initialized → tools/list 或 tools/call；直接支持 JSON 响应。核心执行器已实现握手，无需未锁版 `npx -y mcp-remote`。认证由宿主注入 Bearer token；配置包不含真实凭据。

工具 schema 以运行中的 `tools/list` 与 Go `mcp_server.go` 为准，HTTP API 以 `routes.go` 为准。下表解释日常任务用到的字段，不复制所有上游能力保证。

| 动作 | MCP 工具 | HTTP API |
| --- | --- | --- |
| 状态 | check_login_status | GET /api/v1/login/status |
| 搜帖 | search_feeds | GET/POST /api/v1/feeds/search |
| 完整帖/评论 | get_feed_detail | POST /api/v1/feeds/detail |
| 本人主页 | get_my_profile | GET /api/v1/user/me |
| 主帖评论 | post_comment_to_feed | POST /api/v1/feeds/comment |
| 回复指定评论 | reply_comment_in_feed | POST /api/v1/feeds/comment/reply |
| 图文 | publish_content | POST /api/v1/publish |
| 视频 | publish_with_video | POST /api/v1/publish_video |
| 查提交 | get_operation_status | GET /api/v1/operations/:id |
| 人工核对未知 | resolve_operation | 仅 MCP，默认禁用 |

评论：feed_id、xsec_token、content；回复再有 comment_id 或 user_id 至少一个。四类内容工具可选operation_id（稳定编号）、run_id（现有批次）与approval_ref（已有授权引用）；HTTP沿用Idempotency-Key头及对应管理字段。管理字段不进入原内容指纹；不能换token、批次号或编号绕过去重。已配置运营桥接时，无编号会在内部保存稳定任务编号，并据唯一有效审稿核对原批次、目标、文字、素材及排期；无有效匹配即报错。

图文：title（平台计长 ≤20）、content、images（本机绝对路径或固定 HTTP(S)；普通图文至少一张）。可选 tags、schedule_at、is_original、visibility、products、activity、long_article_template、topic_template。视频同类但 video 为单个本地文件，保持原字段定义。

schedule_at 有时区 RFC3339，调用时刻后 1 小时至 14 天。activity 空=不关联，有值却无匹配/入口报错；is_original=true 设置失败报错；topic_template 是原生话题模板，不是长文排版。原创/活动/内容类型要求不可为过测试而移除。群绑定、禁止复制尚无接口；必须项缺失就停。

发布不将跳到首页/登录/验证页、正文或隐藏旧提示当成功；定时弹窗确认不重点主发布按钮。素材、标题和要求的长文标签未确认就停，详见 [发布恢复](PUBLISH-RECOVERY.md)。

check_login_status检查主站实际身份；创作平台和云端/本机状态分别核验，不能借另一入口当已登录。

所有外部写动作统一 XHS_ALLOW_WRITE=1、实际身份、串行、持久间隔和提交账本，包括上游点赞/收藏/通知回复/私信，不能从备用接口绕过。二维码、登录短信/删除会话另有默认关闭的 XHS_ALLOW_LOGIN_ACTIONS；推荐私密登录/人工接管，不把密码或验证码放进聊天/交付物。

错误是实际工具 IsError 或 HTTP 错误；正文包含 ERR_* 错误码。ERR_OPERATION_UNKNOWN 必须先查询。操作状态为 in_flight、succeeded、unknown、confirmed（人工证实已提交）、not_applied（已明确未调用发送或人工证实未提交）。succeeded 是自动化确认得到的结果，尚未提供统一平台笔记/评论 ID；平台凭证需真实验收收集，不编造链接。

操作记录保存本次请求指纹，operation_id 改参数冲突；记录成功重放不会再点提交。同一内容换编号也拒绝重复。评论执行明确未调用发送时返回 ERR_COMMENT_NOT_SUBMITTED 并保存阶段；导航/页面等待超时可带 ERR_COMMENT_TEMPORARY_PAGE_FAILURE，执行器只对此有限延后。开始发送后未知以及其他未明确的失败采取保守 unknown。详细等待与确认见 [评论超时](COMMENT-TIMEOUT.md)。本程序没有跨平台事务或 exactly-once 保证；状态丢失或平台无法查询时必须人工处理。

旧权威契约已退役，不能沿用“写闸常开”“发帖独立于闸”“失败重试5次”“不保存发布凭证”等条款。

浏览器未连接返回 `ERR_BROWSER_UNAVAILABLE`，与账号掉线分开。读任务保存并暂停；写入中失联没有未提交证明，仍查结果后处理。

普通搜索、读取、身份与诊断直接调用对应工具，不创建任务或批次。post_comment_to_feed、reply_comment_in_feed、publish_content、publish_with_video一次调用内部完成真实审稿匹配、固定素材、auto_run=false任务保存、执行与Go持久回执归账，不唤醒旧队列。运行中的原队列任务复用原编号，仍由原队列归账。

submit_task仅供需要跨时间或重启后自动继续的长任务，字段仍为tool、payload、approval_ref、not_before、dependency、run_id、auto_run；get_task_status按原编号查询；control_task_queue暂停新领取；resume_task凭处理依据恢复blocked。主站检查支持fresh，搜索和详情支持fresh/compact/result_limit；fresh不绕过预算。

manage_operation_run(operation,data)沿用configure、begin、update、cancel、schedule、review、finish、accept、maintain、status、repair。accept仍需真实用户验收引用与各启用流程的成功实测；工具不运行模型、不创建宿主日程、不修改skill。内容审查仍绑定私有配置版本、source/draft/edited/final文件、计划与最终参数，不能以旧checks布尔值替代证据。格式见[内容审查契约](CONTENT-REVIEW.md)与[持续运营字段](../skills/xiaohongshu-operations/references/continuous-operations.md)。

四类队列写入对发送前的明确失败记录not_applied，返回ERR_ACTION_NOT_SUBMITTED或原评论错误；只有受跟踪的实际发送点之前才作此判定。点击调用开始后即使没有回包仍为unknown；没有追踪的其他写工具继续保守处理。

cancel仅在原任务仍queued时原子成功；schedule替换未绑定或已取消/明确失败的位置，保留已提交和仍有任务的位置；finish返回outcomes，允许明确失败与成功条目一起结案，unknown/blocked/running仍保留。repair必须给verification_report，核对冻结版本、必需能力、退出码和实际文件哈希，不绑定工具总数。

unknown自动停队由外围队列负责。用户明确新增授权后可直接调用不同目标，原未知记录不变；同编号或内容指纹的未知/成功操作仍不能换编号重发。直接工具不启用旧队列。

reconcile_task(task_id)继续兼容，用于修复历史漏记账，只查询本地原编号Go回执，不导航或发送。四类内容工具均核对工具、完整内容指纹、目标、账号、成功标记和回执时间；未知、缺失、不匹配的回执不能记成功。旧手动submit_task→直接调用→reconcile仍可用，普通发送不再要求agent执行这三步。

本地节奏未到会返回ERR_RATE_LIMIT和retry_at；沿返回原编号到点继续，不循环查询或长睡。必要素材、话题、预约时间不因失败而静默删除，unknown与提交记录必须先查清；完整执行与恢复规则只维护在[RUNTIME.md](RUNTIME.md)。
