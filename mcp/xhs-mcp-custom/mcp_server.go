package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"runtime/debug"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
)

// Helper functions for annotation pointers
func boolPtr(b bool) *bool { return &b }

// MCP 工具参数结构体定义

// PublishContentArgs 发布内容的参数
type PublishContentArgs struct {
	RunID               string   `json:"run_id,omitempty" jsonschema:"可选已审稿批次；省略时按当前账号准确稿件唯一匹配"`
	ApprovalRef         string   `json:"approval_ref,omitempty" jsonschema:"可选已有授权记录引用；不替代实际授权"`
	OperationID         string   `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	Title               string   `json:"title" jsonschema:"内容标题（小红书限制：最多20个中文字或英文单词）"`
	Content             string   `json:"content" jsonschema:"正文内容，不包含以#开头的标签内容，所有话题标签都用tags参数来生成和提供即可"`
	Images              []string `json:"images,omitempty" jsonschema:"图片路径列表。普通图文至少1张（HTTP/本地绝对路径）；若设 long_article_template 可空"`
	Tags                []string `json:"tags,omitempty" jsonschema:"话题标签列表（可选参数），如 [美食, 旅行, 生活]"`
	ScheduleAt          string   `json:"schedule_at,omitempty" jsonschema:"定时发布时间（可选），ISO8601格式如 2024-01-20T10:30:00+08:00，支持1小时至14天内。不填则立即发布"`
	IsOriginal          bool     `json:"is_original,omitempty" jsonschema:"是否声明原创（可选），true为声明原创，false或不填则不声明"`
	Visibility          string   `json:"visibility,omitempty" jsonschema:"可见范围（可选），支持: 公开可见(默认)、仅自己可见、仅互关好友可见。不填则默认公开可见"`
	Products            []string `json:"products,omitempty" jsonschema:"商品关键词列表（可选），用于绑定带货商品。填写商品名称或商品ID，系统会自动搜索并选择第一个匹配结果。需账号已开通商品功能。示例: [面膜, 防晒霜SPF50]"`
	Activity            string   `json:"activity,omitempty" jsonschema:"关联活动名称（可选，别名 activity_name）。按发布页活动卡片 .activity-name 包含匹配；空则跳过；有值但页面无入口或无匹配卡片时明确报错中止"`
	LongArticleTemplate string   `json:"long_article_template,omitempty" jsonschema:"长文一键排版模版名（可选）。走写长文→新的创作→一键排版→.template-card；匹配 .template-title。与 topic_template 无关。找不到卡片明确报错。设此参时 images 可空"`
	TopicTemplate       string   `json:"topic_template,omitempty" jsonschema:"话题模版名（可选，如 answereq）。发布页 #话题旁下拉→使用/管理话题模版→按卡片名点应用，插入原生话题标签。与 long_article_template 分开；找不到则报错中止"`
}

// PublishVideoArgs 发布视频的参数（仅支持本地单个视频文件）
type PublishVideoArgs struct {
	RunID       string   `json:"run_id,omitempty" jsonschema:"可选已审稿批次；省略时按当前账号准确稿件唯一匹配"`
	ApprovalRef string   `json:"approval_ref,omitempty" jsonschema:"可选已有授权记录引用；不替代实际授权"`
	OperationID string   `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	Title       string   `json:"title" jsonschema:"内容标题（小红书限制：最多20个中文字或英文单词）"`
	Content     string   `json:"content" jsonschema:"正文内容，不包含以#开头的标签内容，所有话题标签都用tags参数来生成和提供即可"`
	Video       string   `json:"video" jsonschema:"本地视频绝对路径（仅支持单个视频文件，如:/srv/xhs/output/video.mp4）"`
	Tags        []string `json:"tags,omitempty" jsonschema:"话题标签列表（可选参数），如 [美食, 旅行, 生活]"`
	ScheduleAt  string   `json:"schedule_at,omitempty" jsonschema:"定时发布时间（可选），ISO8601格式如 2024-01-20T10:30:00+08:00，支持1小时至14天内。不填则立即发布"`
	IsOriginal  bool     `json:"is_original,omitempty" jsonschema:"是否声明原创（可选），true为声明原创，false或不填则不声明；请求原创但设置失败则中止发布"`
	Visibility  string   `json:"visibility,omitempty" jsonschema:"可见范围（可选），支持: 公开可见(默认)、仅自己可见、仅互关好友可见。不填则默认公开可见"`
	Products    []string `json:"products,omitempty" jsonschema:"商品关键词列表（可选），用于绑定带货商品。填写商品名称或商品ID，系统会自动搜索并选择第一个匹配结果。需账号已开通商品功能。示例: [面膜, 防晒霜SPF50]"`
	Activity    string   `json:"activity,omitempty" jsonschema:"关联活动名称（可选，别名 activity_name）。按发布页活动卡片 .activity-name 包含匹配；空则跳过；有值但页面无入口或无匹配卡片时明确报错中止"`
}

// SearchFeedsArgs 搜索内容的参数
type SearchFeedsArgs struct {
	Fresh       bool         `json:"fresh,omitempty" jsonschema:"跳过短期读取缓存，重新检查当前页面"`
	Compact     bool         `json:"compact,omitempty" jsonschema:"省略图片/视频等大字段，保留文本、目标编号与互动数据"`
	ResultLimit int          `json:"result_limit,omitempty" jsonschema:"限制返回的列表/评论条数，1..100；返回截断标记，不改变实际采集量"`
	Keyword     string       `json:"keyword" jsonschema:"搜索关键词"`
	Filters     FilterOption `json:"filters,omitempty" jsonschema:"筛选选项"`
}

// FilterOption 筛选选项结构体
type FilterOption struct {
	SortBy      string `json:"sort_by,omitempty" jsonschema:"排序依据: 综合|最新|最多点赞|最多评论|最多收藏,默认为'综合'"`
	NoteType    string `json:"note_type,omitempty" jsonschema:"笔记类型: 不限|视频|图文,默认为'不限'"`
	PublishTime string `json:"publish_time,omitempty" jsonschema:"发布时间: 不限|一天内|一周内|半年内,默认为'不限'"`
	SearchScope string `json:"search_scope,omitempty" jsonschema:"搜索范围: 不限|已看过|未看过|已关注,默认为'不限'"`
	Location    string `json:"location,omitempty" jsonschema:"位置距离: 不限|同城|附近,默认为'不限'"`
}

// FeedDetailArgs 获取Feed详情的参数
type FeedDetailArgs struct {
	Fresh            bool   `json:"fresh,omitempty" jsonschema:"跳过短期读取缓存，重新检查当前页面"`
	Compact          bool   `json:"compact,omitempty" jsonschema:"省略图片/视频等大字段，保留文本、目标编号与互动数据"`
	ResultLimit      int    `json:"result_limit,omitempty" jsonschema:"限制返回的列表/评论条数，1..100；返回截断标记，不改变实际采集量"`
	FeedID           string `json:"feed_id" jsonschema:"小红书笔记ID，从Feed列表获取"`
	XsecToken        string `json:"xsec_token" jsonschema:"访问令牌，从Feed列表的xsecToken字段获取"`
	LoadAllComments  bool   `json:"load_all_comments,omitempty" jsonschema:"是否加载全部评论。false仅返回前10条一级评论（默认），true滚动加载更多评论"`
	Limit            int    `json:"limit,omitempty" jsonschema:"【仅当load_all_comments为true时生效】限制加载的一级评论数量。例如20表示最多加载20条，默认20"`
	ClickMoreReplies bool   `json:"click_more_replies,omitempty" jsonschema:"【仅当load_all_comments为true时生效】是否展开二级回复。true展开子评论，false不展开（默认）"`
	ReplyLimit       int    `json:"reply_limit,omitempty" jsonschema:"【仅当click_more_replies为true时生效】跳过回复数过多的评论。例如10表示跳过超过10条回复的，默认10"`
	ScrollSpeed      string `json:"scroll_speed,omitempty" jsonschema:"【仅当load_all_comments为true时生效】滚动速度slow慢速、normal正常、fast快速"`
}

// UserProfileArgs 获取用户主页的参数
type UserProfileArgs struct {
	UserID    string `json:"user_id" jsonschema:"小红书用户ID，从Feed列表获取"`
	XsecToken string `json:"xsec_token" jsonschema:"访问令牌，从Feed列表的xsecToken字段获取"`
	Tab       string `json:"tab,omitempty" jsonschema:"主页 tab: note(笔记,默认)|fav(收藏)|liked(点赞)。收藏和点赞可能被对方设为不公开"`
}

// MyProfileArgs 我的主页参数
type MyProfileArgs struct {
	Tab string `json:"tab,omitempty" jsonschema:"主页 tab: note(笔记,默认)|fav(收藏)|liked(点赞)"`
}

// LoginWithSMSArgs 短信登录参数（验证码由聊天传入，不用自动接码平台）
type LoginWithSMSArgs struct {
	Phone string `json:"phone" jsonschema:"手机号"`
	Code  string `json:"code,omitempty" jsonschema:"短信验证码。首次可空以触发发码；收到后再带 code 调用"`
}

// PostCommentArgs 发表一级评论的参数。
// 写操作需环境变量 XHS_ALLOW_WRITE=1，否则返回「写操作未开启」。
type PostCommentArgs struct {
	RunID       string `json:"run_id,omitempty" jsonschema:"可选已审稿批次；省略时按当前账号准确稿件唯一匹配"`
	ApprovalRef string `json:"approval_ref,omitempty" jsonschema:"可选已有授权记录引用；不替代实际授权"`
	OperationID string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	FeedID      string `json:"feed_id" jsonschema:"小红书笔记ID，从 list_feeds / search_feeds 获取"`
	XsecToken   string `json:"xsec_token" jsonschema:"访问令牌，与 feed 同条的 xsecToken 字段"`
	Content     string `json:"content" jsonschema:"评论内容"`
}

// ReplyCommentArgs 回复评论的参数（评中评）。
// 必填：feed_id、xsec_token、content；comment_id 与 user_id 至少一个。
// 写操作需环境变量 XHS_ALLOW_WRITE=1，否则返回「写操作未开启」。
type ReplyCommentArgs struct {
	RunID       string `json:"run_id,omitempty" jsonschema:"可选已审稿批次；省略时按当前账号准确稿件唯一匹配"`
	ApprovalRef string `json:"approval_ref,omitempty" jsonschema:"可选已有授权记录引用；不替代实际授权"`
	OperationID string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	FeedID      string `json:"feed_id" jsonschema:"小红书笔记ID，从 list_feeds / search_feeds / get_feed_detail 获取"`
	XsecToken   string `json:"xsec_token" jsonschema:"访问令牌，与 feed 同条的 xsecToken 字段"`
	CommentID   string `json:"comment_id,omitempty" jsonschema:"目标评论ID（优先），从 get_feed_detail 评论列表的 id 获取；与 user_id 二选一或同时传"`
	UserID      string `json:"user_id,omitempty" jsonschema:"目标评论用户ID，评论区 data-user-id；无 comment_id 时用此匹配第一条该用户评论"`
	Content     string `json:"content" jsonschema:"回复内容"`
}

// LikeFeedArgs 点赞参数
type LikeFeedArgs struct {
	OperationID string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	FeedID      string `json:"feed_id" jsonschema:"小红书笔记ID，从Feed列表获取"`
	XsecToken   string `json:"xsec_token" jsonschema:"访问令牌，从Feed列表的xsecToken字段获取"`
	Unlike      bool   `json:"unlike,omitempty" jsonschema:"是否取消点赞，true为取消点赞，false或未设置则为点赞"`
}

// FavoriteFeedArgs 收藏参数
type FavoriteFeedArgs struct {
	OperationID string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	FeedID      string `json:"feed_id" jsonschema:"小红书笔记ID，从Feed列表获取"`
	XsecToken   string `json:"xsec_token" jsonschema:"访问令牌，从Feed列表的xsecToken字段获取"`
	Unfavorite  bool   `json:"unfavorite,omitempty" jsonschema:"是否取消收藏，true为取消收藏，false或未设置则为收藏"`
}

// ListNotificationsArgs 通知列表参数
type ListNotificationsArgs struct {
	Tab   string `json:"tab,omitempty" jsonschema:"通知分区: mentions(评论和@,默认)|likes(赞和收藏)|connections(新增关注)"`
	Limit int    `json:"limit,omitempty" jsonschema:"返回条数上限，默认20"`
}

// LikeNotificationArgs 通知点赞参数
type LikeNotificationArgs struct {
	OperationID string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	CommentID   string `json:"comment_id" jsonschema:"目标评论ID，从 list_notifications 的 comment_id 字段获取"`
	Unlike      bool   `json:"unlike,omitempty" jsonschema:"是否取消点赞，true为取消点赞，false或未设置则为点赞"`
}

// ReplyNotificationArgs 通知回复参数
type ReplyNotificationArgs struct {
	OperationID string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	CommentID   string `json:"comment_id" jsonschema:"目标评论ID，从 list_notifications 的 comment_id 字段获取"`
	Content     string `json:"content" jsonschema:"回复内容"`
}

// ListDirectMessagesArgs 私信会话列表（只读）
type ListDirectMessagesArgs struct {
	Limit int `json:"limit,omitempty" jsonschema:"返回会话条数上限，默认30"`
}

// ReplyDirectMessageArgs 私信回复/打开会话。
// 真正发送需将 XHS_ALLOW_WRITE 设为 1；open_only 为 true 时仅打开会话不发送、不要求写闸。
type ReplyDirectMessageArgs struct {
	OperationID    string `json:"operation_id,omitempty" jsonschema:"调用方稳定的提交编号；同编号同参数不重复提交，换参数报冲突"`
	ConversationID string `json:"conversation_id,omitempty" jsonschema:"会话ID（优先），打开后 URL 形如 /chat/ID；与 name 至少一个"`
	Name           string `json:"name,omitempty" jsonschema:"会话显示名（侧栏 .xhs-im-conv-item__name）；与 conversation_id 至少一个"`
	Content        string `json:"content,omitempty" jsonschema:"回复正文；open_only 为 true 时可空"`
	OpenOnly       bool   `json:"open_only,omitempty" jsonschema:"为 true 时只打开会话不发送（半自动）；省略或 false 时发送（需环境变量 XHS_ALLOW_WRITE 为 1）"`
}

// InitMCPServer 初始化 MCP Server
func InitMCPServer(appServer *AppServer) *mcp.Server {
	// 创建 MCP Server
	server := mcp.NewServer(
		&mcp.Implementation{
			Name:    "xhs-mcp-custom",
			Version: version,
		},
		nil,
	)

	// 注册所有工具
	registerTools(server, appServer)
	registerRuntimeTools(server, appServer)

	logrus.Info("MCP Server initialized with official SDK")

	return server
}

func withPanicRecovery[T any](
	toolName string,
	handler func(context.Context, *mcp.CallToolRequest, T) (*mcp.CallToolResult, any, error),
) func(context.Context, *mcp.CallToolRequest, T) (*mcp.CallToolResult, any, error) {

	return func(ctx context.Context, req *mcp.CallToolRequest, args T) (result *mcp.CallToolResult, resp any, err error) {
		taskID, _ := req.Params.Meta["xhs/task_id"].(string)
		var notify func(string, float64)
		if token := req.Params.Meta["progressToken"]; token != nil {
			notify = func(stage string, step float64) {
				n, c := context.WithTimeout(ctx, time.Second)
				defer c()
				_ = req.Session.NotifyProgress(n, &mcp.ProgressNotificationParams{ProgressToken: token, Progress: step, Message: stage})
			}
		}
		ctx, finish := execution.Begin(ctx, toolName, taskID, notify)
		defer func() { finish(err == nil && result != nil && !result.IsError) }()

		defer func() {
			if r := recover(); r != nil {
				if failure, ok := r.(error); ok {
					if errors.Is(failure, context.Canceled) || errors.Is(failure, context.DeadlineExceeded) {
						result = &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: "ERR_TIMEOUT: phase=" + execution.CurrentStage(ctx) + "; progress preserved"}}}
						resp, err = nil, nil
						return
					}
					var unavailable *browser.UnavailableError
					if errors.As(failure, &unavailable) {
						logrus.WithField("tool", toolName).Warn("宿主浏览器不可连接；保留进度与原会话")
						result = &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: unavailable.Error()}}, IsError: true}
						resp, err = nil, nil
						return
					}
				}
				logrus.WithFields(logrus.Fields{
					"tool":  toolName,
					"panic": r,
				}).Error("Tool handler panicked")

				logrus.Errorf("Stack trace:\n%s", debug.Stack())

				result = &mcp.CallToolResult{
					Content: []mcp.Content{
						&mcp.TextContent{
							Text: fmt.Sprintf("工具 %s 执行时发生内部错误: %v\n\n请查看服务端日志获取详细信息。", toolName, r),
						},
					},
					IsError: true,
				}
				resp = nil
				err = nil
			}
		}()

		bounded, cancel := context.WithTimeout(ctx, toolTimeout())
		defer cancel()
		raw, _ := json.Marshal(args)
		if len(req.Params.Arguments) > 0 {
			raw = req.Params.Arguments
		}
		bounded = withDirectContentRequest(bounded, raw)
		var read struct {
			Fresh   bool `json:"fresh"`
			Compact bool `json:"compact"`
			Limit   int  `json:"result_limit"`
		}
		_ = json.Unmarshal(raw, &read)
		if read.Limit < 0 || read.Limit > 100 {
			return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: "ERR_READ_OPTIONS: result_limit must be 0..100"}}}, nil, nil
		}
		bounded = context.WithValue(bounded, freshReadKey{}, read.Fresh)
		var meta struct {
			OperationID string `json:"operation_id"`
		}
		_ = json.Unmarshal(raw, &meta)
		if meta.OperationID != "" {
			bounded = withOperationID(bounded, meta.OperationID)
		}
		result, resp, err = handler(bounded, req, args)
		if toolName == "search_feeds" || toolName == "get_feed_detail" {
			projectReadResult(result, read.Compact, read.Limit)
		}
		return result, resp, err
	}
}

// registerTools 注册所有 MCP 工具
func registerTools(server *mcp.Server, appServer *AppServer) {
	// 工具 1: 检查登录状态
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "check_login_status",
			Description: "检查小红书主站的当前账号身份（首页用户身份，使用短缓存；不搜索、不只看 cookie）。创作平台登录不能替代主站身份，云端与本机分别检查。",
			Annotations: &mcp.ToolAnnotations{
				Title:        "Check Login Status",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("check_login_status", func(ctx context.Context, req *mcp.CallToolRequest, _ struct {
			Fresh bool `json:"fresh,omitempty" jsonschema:"跳过短期身份检查缓存；不触发重新登录或绕过验证"`
		}) (*mcp.CallToolResult, any, error) {
			result := appServer.handleCheckLoginStatus(ctx)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 2: 获取登录二维码
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "get_login_qrcode",
			Description: "获取登录二维码（多选择器+空白/薯队长检测与重试；返回 Base64 图片和超时时间）",
			Annotations: &mcp.ToolAnnotations{
				Title:        "Get Login QR Code",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("get_login_qrcode", func(ctx context.Context, req *mcp.CallToolRequest, _ any) (*mcp.CallToolResult, any, error) {
			result := appServer.handleGetLoginQrcode(ctx)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具: 短信验证码登录
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "login_with_sms",
			Description: "短信验证码登录。首次只传 phone 触发发码；把聊天里收到的验证码填入 code 再调一次。不使用自动接码平台。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Login With SMS",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("login_with_sms", func(ctx context.Context, req *mcp.CallToolRequest, args LoginWithSMSArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleLoginWithSMS(ctx, args.Phone, args.Code)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 3: 删除 cookies（登录重置）
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "delete_cookies",
			Description: "删除 cookies 文件，重置登录状态。删除后需要重新登录。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Delete Cookies",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("delete_cookies", func(ctx context.Context, req *mcp.CallToolRequest, _ any) (*mcp.CallToolResult, any, error) {
			result := appServer.handleDeleteCookies(ctx)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 4: 发布内容
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "publish_content",
			Description: "发布小红书图文；配置运营运行时后，一次调用完成准确审稿匹配、已有授权核对、发送和回执归账。run_id/approval_ref可选，原编号和防重发沿用。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Publish Content",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("publish_content", func(ctx context.Context, req *mcp.CallToolRequest, args PublishContentArgs) (*mcp.CallToolResult, any, error) {
			// 转换参数格式到现有的 handler
			argsMap := map[string]interface{}{
				"title":                 args.Title,
				"content":               args.Content,
				"images":                convertStringsToInterfaces(args.Images),
				"tags":                  convertStringsToInterfaces(args.Tags),
				"schedule_at":           args.ScheduleAt,
				"is_original":           args.IsOriginal,
				"visibility":            args.Visibility,
				"products":              convertStringsToInterfaces(args.Products),
				"activity":              args.Activity,
				"long_article_template": args.LongArticleTemplate,
				"topic_template":        args.TopicTemplate,
			}
			result := appServer.handlePublishContent(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 5: 获取Feed列表
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "list_feeds",
			Description: "获取首页 Feeds 列表",
			Annotations: &mcp.ToolAnnotations{
				Title:        "List Feeds",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("list_feeds", func(ctx context.Context, req *mcp.CallToolRequest, _ any) (*mcp.CallToolResult, any, error) {
			result := appServer.handleListFeeds(ctx)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 6: 搜索内容
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "search_feeds",
			Description: "搜索小红书内容（需要已登录）",
			Annotations: &mcp.ToolAnnotations{
				Title:        "Search Feeds",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("search_feeds", func(ctx context.Context, req *mcp.CallToolRequest, args SearchFeedsArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleSearchFeeds(ctx, args)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 7: 获取Feed详情
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "get_feed_detail",
			Description: "获取小红书笔记详情，返回笔记内容、图片、作者信息、互动数据（点赞/收藏/分享数）及评论列表。视频笔记额外返回 video 字段，含各编码档位的视频直链与字幕地址（均带签名、有时效）。默认返回前10条一级评论，如需更多评论请将 load_all_comments 设为 true",
			Annotations: &mcp.ToolAnnotations{
				Title:        "Get Feed Detail",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("get_feed_detail", func(ctx context.Context, req *mcp.CallToolRequest, args FeedDetailArgs) (*mcp.CallToolResult, any, error) {
			argsMap := map[string]interface{}{
				"feed_id":           args.FeedID,
				"xsec_token":        args.XsecToken,
				"load_all_comments": args.LoadAllComments,
			}

			// 只有当 load_all_comments 为 true 时，才处理其他参数
			if args.LoadAllComments {
				argsMap["click_more_replies"] = args.ClickMoreReplies

				// 设置评论数量限制，默认20
				limit := args.Limit
				if limit <= 0 {
					limit = 20
				}
				argsMap["max_comment_items"] = limit

				// 设置回复数量阈值，默认10
				replyLimit := args.ReplyLimit
				if replyLimit <= 0 {
					replyLimit = 10
				}
				argsMap["max_replies_threshold"] = replyLimit

				if args.ScrollSpeed != "" {
					argsMap["scroll_speed"] = args.ScrollSpeed
				}
			}

			result := appServer.handleGetFeedDetail(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 8: 获取用户主页
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "user_profile",
			Description: "获取指定的小红书用户主页，返回用户基本信息，关注、粉丝、获赞量，以及指定 tab 下的内容。tab 可选 note(笔记,默认)、fav(收藏)、liked(点赞)，后两者可能被对方设为不公开",
			Annotations: &mcp.ToolAnnotations{
				Title:        "User Profile",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("user_profile", func(ctx context.Context, req *mcp.CallToolRequest, args UserProfileArgs) (*mcp.CallToolResult, any, error) {
			argsMap := map[string]interface{}{
				"user_id":    args.UserID,
				"xsec_token": args.XsecToken,
				"tab":        args.Tab,
			}
			result := appServer.handleUserProfile(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 9: 发表评论
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "post_comment_to_feed",
			Description: "发表一级评论到小红书笔记；配置运营运行时后，一次调用完成准确审稿匹配、已有授权核对、发送和回执归账。参数：feed_id、xsec_token、content；run_id/approval_ref可选。需开启写操作。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Post Comment",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("post_comment_to_feed", func(ctx context.Context, req *mcp.CallToolRequest, args PostCommentArgs) (*mcp.CallToolResult, any, error) {
			argsMap := map[string]interface{}{
				"feed_id":    args.FeedID,
				"xsec_token": args.XsecToken,
				"content":    args.Content,
			}
			result := appServer.handlePostComment(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 10: 回复评论
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "reply_comment_in_feed",
			Description: "回复笔记下指定评论；配置运营运行时后，一次调用完成准确审稿匹配、已有授权核对、发送和回执归账。参数：feed_id、xsec_token、content，以及 comment_id 或 user_id 至少一个；run_id/approval_ref可选。需开启写操作。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Reply Comment",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("reply_comment_in_feed", func(ctx context.Context, req *mcp.CallToolRequest, args ReplyCommentArgs) (*mcp.CallToolResult, any, error) {
			if args.CommentID == "" && args.UserID == "" {
				return &mcp.CallToolResult{
					IsError: true,
					Content: []mcp.Content{&mcp.TextContent{Text: "缺少 comment_id 或 user_id"}},
				}, nil, nil
			}

			argsMap := map[string]interface{}{
				"feed_id":    args.FeedID,
				"xsec_token": args.XsecToken,
				"comment_id": args.CommentID,
				"user_id":    args.UserID,
				"content":    args.Content,
			}
			result := appServer.handleReplyComment(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 11: 发布视频（仅本地文件）
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "publish_with_video",
			Description: "发布小红书视频（本地单个视频文件）；配置运营运行时后，一次调用完成准确审稿匹配、已有授权核对、发送和回执归账。run_id/approval_ref可选。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Publish Video",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("publish_with_video", func(ctx context.Context, req *mcp.CallToolRequest, args PublishVideoArgs) (*mcp.CallToolResult, any, error) {
			argsMap := map[string]interface{}{
				"title":       args.Title,
				"content":     args.Content,
				"video":       args.Video,
				"tags":        convertStringsToInterfaces(args.Tags),
				"schedule_at": args.ScheduleAt,
				"is_original": args.IsOriginal,
				"visibility":  args.Visibility,
				"products":    convertStringsToInterfaces(args.Products),
				"activity":    args.Activity,
			}
			result := appServer.handlePublishVideo(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 12: 点赞笔记
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "like_feed",
			Description: "为指定笔记点赞或取消点赞（如已点赞将跳过点赞，如未点赞将跳过取消点赞）",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Like Feed",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("like_feed", func(ctx context.Context, req *mcp.CallToolRequest, args LikeFeedArgs) (*mcp.CallToolResult, any, error) {
			argsMap := map[string]interface{}{
				"feed_id":    args.FeedID,
				"xsec_token": args.XsecToken,
				"unlike":     args.Unlike,
			}
			result := appServer.handleLikeFeed(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 13: 收藏笔记
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "favorite_feed",
			Description: "收藏指定笔记或取消收藏（如已收藏将跳过收藏，如未收藏将跳过取消收藏）",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Favorite Feed",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("favorite_feed", func(ctx context.Context, req *mcp.CallToolRequest, args FavoriteFeedArgs) (*mcp.CallToolResult, any, error) {
			argsMap := map[string]interface{}{
				"feed_id":    args.FeedID,
				"xsec_token": args.XsecToken,
				"unfavorite": args.Unfavorite,
			}
			result := appServer.handleFavoriteFeed(ctx, argsMap)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 14: 获取我的主页
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "get_my_profile",
			Description: "获取当前登录用户的主页，返回用户基本信息，关注、粉丝、获赞量，以及指定 tab 下的内容。tab 可选 note(自己发的笔记,默认)、fav(自己收藏的)、liked(自己点赞的)",
			Annotations: &mcp.ToolAnnotations{
				Title:        "Get My Profile",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("get_my_profile", func(ctx context.Context, req *mcp.CallToolRequest, args MyProfileArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleGetMyProfile(ctx, args.Tab)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 15: 通知未读数
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "get_unread_count",
			Description: "获取通知未读数，返回「评论和@」「赞和收藏」「新增关注」三个分区各自的未读条数。不会清除未读标记。查看具体内容用 list_notifications。",
			Annotations: &mcp.ToolAnnotations{
				Title:        "Get Unread Count",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("get_unread_count", func(ctx context.Context, req *mcp.CallToolRequest, _ any) (*mcp.CallToolResult, any, error) {
			result := appServer.handleGetUnreadCount(ctx)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 16: 通知列表
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "list_notifications",
			Description: "获取通知列表。返回评论内容、评论者、以及对应笔记的 feed_id 和 xsec_token（可用于 get_feed_detail 读原帖）。已删除或不可见的条目会被过滤，过滤数量见 filtered 字段。注意：会清除该分区的未读标记，只需要未读数时用 get_unread_count。",
			Annotations: &mcp.ToolAnnotations{
				Title:        "List Notifications",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("list_notifications", func(ctx context.Context, req *mcp.CallToolRequest, args ListNotificationsArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleListNotifications(ctx, args.Tab, args.Limit)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 17: 回复通知里的评论
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "reply_notification",
			Description: "回复「评论和@」里的一条评论。comment_id 从 list_notifications 获取。适合处理收到的评论，无需先定位笔记。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Reply Notification",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("reply_notification", func(ctx context.Context, req *mcp.CallToolRequest, args ReplyNotificationArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleReplyNotification(ctx, args.CommentID, args.Content)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具 18: 给通知里的评论点赞
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "like_notification",
			Description: "给「评论和@」里的一条评论点赞或取消点赞。comment_id 从 list_notifications 获取（其 liked 字段是当前状态）。已是目标状态时跳过。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Like Notification",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("like_notification", func(ctx context.Context, req *mcp.CallToolRequest, args LikeNotificationArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleLikeNotification(ctx, args.CommentID, args.Unlike)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具: 私信会话列表（只读）
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "list_direct_messages",
			Description: "列出小红书私信会话（www /chat 侧栏）。只读。返回 name/time/summary/pinned；conversation_id 需打开会话后从 URL 获取。通知 list_notifications ≠ 私信。",
			Annotations: &mcp.ToolAnnotations{
				Title:        "List Direct Messages",
				ReadOnlyHint: true,
			},
		},
		withPanicRecovery("list_direct_messages", func(ctx context.Context, req *mcp.CallToolRequest, args ListDirectMessagesArgs) (*mcp.CallToolResult, any, error) {
			result := appServer.handleListDirectMessages(ctx, args.Limit)
			return convertToMCPResult(result), nil, nil
		}),
	)

	// 工具: 私信回复 / 打开会话
	mcp.AddTool(server,
		&mcp.Tool{
			Name:        "reply_direct_message",
			Description: "回复私信或仅打开会话。参数：conversation_id 或 name，content；open_only 为 true 时只打开不发送。真正发送需将 XHS_ALLOW_WRITE 设为 1（Enter 提交）。",
			Annotations: &mcp.ToolAnnotations{
				Title:           "Reply Direct Message",
				DestructiveHint: boolPtr(true),
			},
		},
		withPanicRecovery("reply_direct_message", func(ctx context.Context, req *mcp.CallToolRequest, args ReplyDirectMessageArgs) (*mcp.CallToolResult, any, error) {
			if args.ConversationID == "" && args.Name == "" {
				return &mcp.CallToolResult{
					IsError: true,
					Content: []mcp.Content{&mcp.TextContent{Text: "缺少 conversation_id 或 name"}},
				}, nil, nil
			}
			result := appServer.handleReplyDirectMessage(ctx, args.ConversationID, args.Name, args.Content, args.OpenOnly)
			return convertToMCPResult(result), nil, nil
		}),
	)

	logrus.Infof("Registered %d MCP tools", 20)
}

// convertToMCPResult 将自定义的 MCPToolResult 转换为官方 SDK 的格式
func convertToMCPResult(result *MCPToolResult) *mcp.CallToolResult {
	var contents []mcp.Content
	for _, c := range result.Content {
		switch c.Type {
		case "text":
			contents = append(contents, &mcp.TextContent{Text: c.Text})
		case "image":
			// 解码 base64 字符串为 []byte
			imageData, err := base64.StdEncoding.DecodeString(c.Data)
			if err != nil {
				logrus.WithError(err).Error("Failed to decode base64 image data")
				// 如果解码失败，添加错误文本
				contents = append(contents, &mcp.TextContent{
					Text: "图片数据解码失败: " + err.Error(),
				})
			} else {
				contents = append(contents, &mcp.ImageContent{
					Data:     imageData,
					MIMEType: c.MimeType,
				})
			}
		}
	}

	return &mcp.CallToolResult{
		Content: contents,
		IsError: result.IsError,
	}
}

// convertStringsToInterfaces 辅助函数：将 []string 转换为 []interface{}
func convertStringsToInterfaces(strs []string) []interface{} {
	result := make([]interface{}, len(strs))
	for i, s := range strs {
		result[i] = s
	}
	return result
}
