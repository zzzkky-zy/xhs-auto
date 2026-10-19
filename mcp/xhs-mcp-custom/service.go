package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/configs"
	"github.com/xpzouying/xiaohongshu-mcp/cookies"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/pkg/downloader"
	"github.com/xpzouying/xiaohongshu-mcp/pkg/xhsutil"
	"github.com/xpzouying/xiaohongshu-mcp/xiaohongshu"
)

// XiaohongshuService 小红书业务服务
type XiaohongshuService struct {
	writeMu      contextMutex
	state        *stateStore
	stateErr     error
	logins       loginSessions
	login        loginGate       // 短缓存登录闸 + 定时探针状态
	captcha      captchaCooldown // 持久 ERR_CAPTCHA 冷却
	limits       platformRateLimits
	browserMu    contextMutex // attach CDP 页操作互斥，防并发导航竞态
	loginCheck   contextMutex
	readCache    resultCache
	searchCache  resultCache
	detailCache  resultCache
	requestNowFn func() time.Time
	pages        taskPages
}

// NewXiaohongshuService 创建小红书服务实例
func NewXiaohongshuService() *XiaohongshuService {
	return &XiaohongshuService{}
}

// PublishRequest 发布请求
type PublishRequest struct {
	Title               string   `json:"title" binding:"required"`
	Content             string   `json:"content" binding:"required"`
	Images              []string `json:"images,omitempty"` // 普通图文至少 1 张；长文模版路径可空
	Tags                []string `json:"tags,omitempty"`
	ScheduleAt          string   `json:"schedule_at,omitempty"`           // 定时发布时间，ISO8601格式，为空则立即发布
	IsOriginal          bool     `json:"is_original,omitempty"`           // 是否声明原创
	Visibility          string   `json:"visibility,omitempty"`            // 可见范围: "公开可见"(默认), "仅自己可见", "仅互关好友可见"
	Products            []string `json:"products,omitempty"`              // 商品关键词列表，用于绑定带货商品
	Activity            string   `json:"activity,omitempty"`              // 关联活动名称（匹配 .activity-name）；空则跳过
	LongArticleTemplate string   `json:"long_article_template,omitempty"` // 长文一键排版模版名；与 topic_template 分开
	TopicTemplate       string   `json:"topic_template,omitempty"`        // 话题模版名（#话题→使用/管理话题模版→应用）；找不到则报错
}

// LoginStatusResponse 登录状态响应
type LoginStatusResponse struct {
	IsLoggedIn bool   `json:"is_logged_in"`
	Username   string `json:"username,omitempty"` // 当前登录账号的昵称
	UserID     string `json:"user_id,omitempty"`  // 用户唯一标识（个人主页 URL 中的 ID）
}

// LoginQrcodeResponse 登录扫码二维码
type LoginQrcodeResponse struct {
	Timeout    string `json:"timeout"`
	IsLoggedIn bool   `json:"is_logged_in"`
	Img        string `json:"img,omitempty"`
}

// PublishResponse 发布响应
type PublishResponse struct {
	Title   string `json:"title"`
	Content string `json:"content"`
	Images  int    `json:"images"`
	Status  string `json:"status"`
}

// PublishVideoRequest 发布视频请求（仅支持本地单个视频文件）
type PublishVideoRequest struct {
	Title      string   `json:"title" binding:"required"`
	Content    string   `json:"content" binding:"required"`
	Video      string   `json:"video" binding:"required"`
	Tags       []string `json:"tags,omitempty"`
	ScheduleAt string   `json:"schedule_at,omitempty"` // 定时发布时间，ISO8601格式，为空则立即发布
	IsOriginal bool     `json:"is_original,omitempty"` // 是否声明原创
	Visibility string   `json:"visibility,omitempty"`  // 可见范围: "公开可见"(默认), "仅自己可见", "仅互关好友可见"
	Products   []string `json:"products,omitempty"`    // 商品关键词列表，用于绑定带货商品
	Activity   string   `json:"activity,omitempty"`    // 关联活动名称（匹配 .activity-name）；空则跳过
}

// PublishVideoResponse 发布视频响应
type PublishVideoResponse struct {
	Title   string `json:"title"`
	Content string `json:"content"`
	Video   string `json:"video"`
	Status  string `json:"status"`
}

// FeedsListResponse Feeds列表响应
type FeedsListResponse struct {
	ReadAt time.Time          `json:"read_at"`
	Cached bool               `json:"cached"`
	Feeds  []xiaohongshu.Feed `json:"feeds"`
	Count  int                `json:"count"`
}

// UserProfileResponse 用户主页响应
type UserProfileResponse struct {
	Cached        bool                           `json:"cached"`
	ReadAt        time.Time                      `json:"read_at"`
	UserBasicInfo xiaohongshu.UserBasicInfo      `json:"userBasicInfo"`
	Interactions  []xiaohongshu.UserInteractions `json:"interactions"`
	Feeds         []xiaohongshu.Feed             `json:"feeds"`
}

// DeleteCookies 删除 cookies 文件，用于登录重置。
// 注意：attach 模式下浏览器会话仍在 宿主浏览器；仅清磁盘备份。要真登出需在桌面清站点数据。
func (s *XiaohongshuService) DeleteCookies(ctx context.Context) error {
	if os.Getenv("XHS_ALLOW_LOGIN_ACTIONS") != "1" {
		return fmt.Errorf("ERR_LOGIN_ACTION_NOT_ALLOWED")
	}
	s.rememberLogin(false)
	cookiePath := cookies.GetCookiesFilePath()
	cookieLoader := cookies.NewLoadCookie(cookiePath)
	return cookieLoader.DeleteCookies()
}

// CheckLoginStatus 通过首页身份检查登录状态；cookie 导出默认关闭。
// 验证码冷却中：不导航，立即返回 ErrCaptcha（保留正登录缓存）。
func (s *XiaohongshuService) CheckLoginStatus(ctx context.Context) (*LoginStatusResponse, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if err := s.errIfRateLimited("identity"); err != nil {
		return nil, err
	}
	if err := s.errIfCaptchaCooldown("check_login_status"); err != nil {
		return nil, err
	}
	if err := s.loginCheck.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.loginCheck.Unlock()
	if hit, ok := s.cachedLoginOK(); hit && ctx.Value(freshReadKey{}) != true {
		execution.Count("login_cache_hits")
		s.login.mu.Lock()
		r := &LoginStatusResponse{IsLoggedIn: ok, UserID: s.login.userID, Username: s.login.nickname}
		s.login.mu.Unlock()
		return r, nil
	}
	execution.Count("login_checks")
	ok, err := s.probeAndSyncLogin(ctx)
	if err != nil {
		return nil, err
	}
	s.login.mu.Lock()
	r := &LoginStatusResponse{IsLoggedIn: ok, UserID: s.login.userID, Username: s.login.nickname}
	s.login.mu.Unlock()
	return r, nil
}

// GetLoginQrcode 获取登录的扫码二维码
func (s *XiaohongshuService) GetLoginQrcode(ctx context.Context) (*LoginQrcodeResponse, error) {
	if os.Getenv("XHS_ALLOW_LOGIN_ACTIONS") != "1" {
		return nil, fmt.Errorf("ERR_LOGIN_ACTION_NOT_ALLOWED: 请在宿主私密接管登录")
	}
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	b := newBrowser()
	page := b.NewPageContext(ctx)

	deferFunc := func() {
		_ = page.Close()
		b.Close()
	}

	loginAction := xiaohongshu.NewLogin(page)

	img, loggedIn, err := loginAction.FetchQrcodeImage(ctx)
	if err != nil || loggedIn {
		defer deferFunc()
	}
	if err != nil {
		s.tripCaptchaCooldown(err)
		return nil, err
	}

	timeout := 4 * time.Minute

	if !loggedIn {
		s.waitScanInBackground(loginAction, page, deferFunc, timeout)
	}

	return &LoginQrcodeResponse{
		Timeout: func() string {
			if loggedIn {
				return "0s"
			}
			return timeout.String()
		}(),
		Img:        img,
		IsLoggedIn: loggedIn,
	}, nil
}

// waitScanInBackground 在后台等用户扫码，扫上了就存 cookie。
//
// 浏览器必须一直活着才检测得到扫码，所以这里不能提前关；但也不能任由它堆积——
// 再取一次二维码就会把上一个还在等的会话关掉，同一时刻只留一个。
func (s *XiaohongshuService) waitScanInBackground(
	loginAction *xiaohongshu.LoginAction, page *rod.Page, closeBrowser func(), timeout time.Duration,
) {
	ctxTimeout, cancel := context.WithTimeout(context.Background(), timeout)
	seq := s.logins.start(cancel)
	logrus.Infof("等待扫码登录，会话 #%d，超时 %s", seq, timeout)

	go func() {
		defer closeBrowser()
		defer cancel()
		defer s.logins.finish(seq)

		if loginAction.WaitForLogin(ctxTimeout) {
			if err := saveCookies(page); err != nil {
				logrus.Errorf("扫码成功但保存 cookies 失败，会话 #%d: %v", seq, err)
				s.rememberLogin(true) // 浏览器已登录，磁盘失败不影响会话
				return
			}
			s.rememberLogin(true)
			logrus.Infof("扫码登录成功，cookies 已保存，会话 #%d", seq)
			return
		}

		// 没等到扫码：要么超时，要么被新取的二维码取代
		logrus.Infof("登录会话 #%d 结束，未检测到扫码（超时或已被新的二维码取代）", seq)
	}()
}

// LoginWithSMSResponse 短信登录响应
type LoginWithSMSResponse struct {
	Success    bool   `json:"success"`
	NeedCode   bool   `json:"need_code,omitempty"`
	Message    string `json:"message"`
	IsLoggedIn bool   `json:"is_logged_in"`
}

// LoginWithSMS 短信验证码登录。code 为空时仅触发发码；code 由聊天/工具参数传入。
func (s *XiaohongshuService) LoginWithSMS(ctx context.Context, phone, code string) (*LoginWithSMSResponse, error) {
	if os.Getenv("XHS_ALLOW_LOGIN_ACTIONS") != "1" {
		return nil, fmt.Errorf("ERR_LOGIN_ACTION_NOT_ALLOWED: 请在宿主私密登录或人工接管，不通过聊天发送密码/验证码")
	}
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewLogin(page)
	err := action.LoginWithSMS(ctx, phone, code)
	if err != nil {
		msg := err.Error()
		if code == "" && (containsNeedCode(msg)) {
			return &LoginWithSMSResponse{
				Success:  false,
				NeedCode: true,
				Message:  msg,
			}, nil
		}
		return nil, err
	}

	if err := saveCookies(page); err != nil {
		s.rememberLogin(true)
		return nil, fmt.Errorf("登录成功但保存 cookies 失败: %w", err)
	}
	s.rememberLogin(true)

	return &LoginWithSMSResponse{
		Success:    true,
		Message:    "短信登录成功，cookies 已保存",
		IsLoggedIn: true,
	}, nil
}

func containsNeedCode(msg string) bool {
	return strings.Contains(msg, "验证码已尝试发送") || strings.Contains(msg, "code 参数")
}

// PublishContent 发布内容
func (s *XiaohongshuService) executePublishContent(ctx context.Context, req *PublishRequest) (*PublishResponse, error) {
	// 验证标题长度（小红书限制：最大20个字）
	if xhsutil.CalcTitleLength(req.Title) > 20 {
		return nil, fmt.Errorf("标题长度超过限制")
	}

	if strings.TrimSpace(req.LongArticleTemplate) == "" && len(req.Images) == 0 {
		return nil, fmt.Errorf("普通图文发布至少需要 1 张图片；长文模版请传 long_article_template")
	}

	var imagePaths []string
	var err error
	if len(req.Images) > 0 {
		execution.Stage(ctx, "download_assets", 90*time.Second)
		imagePaths, err = s.processImages(ctx, req.Images)
		if err != nil {
			return nil, err
		}
	}

	var scheduleTime *time.Time
	if req.ScheduleAt != "" {
		t, err := time.Parse(time.RFC3339, req.ScheduleAt)
		if err != nil {
			return nil, fmt.Errorf("定时发布时间格式错误，请使用 ISO8601 格式: %v", err)
		}

		// 校验定时发布时间范围：1小时至14天
		now := time.Now()
		minTime := now.Add(1 * time.Hour)
		maxTime := now.Add(14 * 24 * time.Hour)

		if t.Before(minTime) {
			return nil, fmt.Errorf("定时发布时间必须至少在1小时后，当前设置: %s，最早可选: %s",
				t.Format("2006-01-02 15:04"), minTime.Format("2006-01-02 15:04"))
		}
		if t.After(maxTime) {
			return nil, fmt.Errorf("定时发布时间不能超过14天，当前设置: %s，最晚可选: %s",
				t.Format("2006-01-02 15:04"), maxTime.Format("2006-01-02 15:04"))
		}

		scheduleTime = &t
		logrus.Infof("设置定时发布时间: %s", t.Format("2006-01-02 15:04"))
	}

	content := xiaohongshu.PublishImageContent{
		Title:               req.Title,
		Content:             req.Content,
		Tags:                req.Tags,
		ImagePaths:          imagePaths,
		ScheduleTime:        scheduleTime,
		IsOriginal:          req.IsOriginal,
		Visibility:          req.Visibility,
		Products:            req.Products,
		Activity:            req.Activity,
		LongArticleTemplate: req.LongArticleTemplate,
		TopicTemplate:       req.TopicTemplate,
	}

	if err := s.publishContent(ctx, content); err != nil {
		logrus.Errorf("发布内容失败: title=%s %v", content.Title, err)
		return nil, err
	}

	response := &PublishResponse{
		Title:   req.Title,
		Content: req.Content,
		Images:  len(imagePaths),
		Status:  "发布完成",
	}

	return response, nil
}

// processImages 处理图片列表，支持URL下载和本地路径
func (s *XiaohongshuService) processImages(ctx context.Context, images []string) ([]string, error) {
	processor := downloader.NewImageProcessor()
	paths := make([]string, 0, len(images))
	for _, image := range images {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		part, err := processor.ProcessImages([]string{image})
		if err != nil {
			return nil, err
		}
		paths = append(paths, part...)
	}
	return paths, nil
}

// publishContent 执行内容发布。整段持 browserMu，避免定时探针/check_login 与发帖并发抢 CDP。
func (s *XiaohongshuService) publishContent(ctx context.Context, content xiaohongshu.PublishImageContent) error {
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	_, err := withTaskPage(s, ctx, true, func(page *rod.Page) (*struct{}, error) {
		action, err := xiaohongshu.NewPublishImageAction(page)
		if err != nil {
			return nil, err
		}
		return &struct{}{}, action.Publish(ctx, content)
	})
	return err
}

// PublishVideo 发布视频（本地文件）
func (s *XiaohongshuService) executePublishVideo(ctx context.Context, req *PublishVideoRequest) (*PublishVideoResponse, error) {
	// 标题长度校验（小红书限制：最大20个字）
	if xhsutil.CalcTitleLength(req.Title) > 20 {
		return nil, fmt.Errorf("标题长度超过限制")
	}

	// 本地视频文件校验
	if req.Video == "" {
		return nil, fmt.Errorf("必须提供本地视频文件")
	}
	if _, err := os.Stat(req.Video); err != nil {
		return nil, fmt.Errorf("视频文件不存在或不可访问: %v", err)
	}

	var scheduleTime *time.Time
	if req.ScheduleAt != "" {
		t, err := time.Parse(time.RFC3339, req.ScheduleAt)
		if err != nil {
			return nil, fmt.Errorf("定时发布时间格式错误，请使用 ISO8601 格式: %v", err)
		}

		// 校验定时发布时间范围：1小时至14天
		now := time.Now()
		minTime := now.Add(1 * time.Hour)
		maxTime := now.Add(14 * 24 * time.Hour)

		if t.Before(minTime) {
			return nil, fmt.Errorf("定时发布时间必须至少在1小时后，当前设置: %s，最早可选: %s",
				t.Format("2006-01-02 15:04"), minTime.Format("2006-01-02 15:04"))
		}
		if t.After(maxTime) {
			return nil, fmt.Errorf("定时发布时间不能超过14天，当前设置: %s，最晚可选: %s",
				t.Format("2006-01-02 15:04"), maxTime.Format("2006-01-02 15:04"))
		}

		scheduleTime = &t
		logrus.Infof("设置定时发布时间: %s", t.Format("2006-01-02 15:04"))
	}

	content := xiaohongshu.PublishVideoContent{
		Title:        req.Title,
		Content:      req.Content,
		Tags:         req.Tags,
		VideoPath:    req.Video,
		ScheduleTime: scheduleTime,
		IsOriginal:   req.IsOriginal,
		Visibility:   req.Visibility,
		Products:     req.Products,
		Activity:     req.Activity,
	}

	if err := s.publishVideo(ctx, content); err != nil {
		return nil, err
	}

	resp := &PublishVideoResponse{
		Title:   req.Title,
		Content: req.Content,
		Video:   req.Video,
		Status:  "发布完成",
	}
	return resp, nil
}

// publishVideo 执行视频发布。整段持 browserMu，避免定时探针/check_login 与发帖并发抢 CDP。
func (s *XiaohongshuService) publishVideo(ctx context.Context, content xiaohongshu.PublishVideoContent) error {
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	_, err := withTaskPage(s, ctx, true, func(page *rod.Page) (*struct{}, error) {
		action, err := xiaohongshu.NewPublishVideoAction(page)
		if err != nil {
			return nil, err
		}
		return &struct{}{}, action.PublishVideo(ctx, content)
	})
	return err
}

// ListFeeds 获取Feeds列表
func (s *XiaohongshuService) ListFeeds(ctx context.Context) (*FeedsListResponse, error) {
	var cached FeedsListResponse
	key, hit, err := prepareRead(s, ctx, "ListFeeds", nil, &s.readCache, &cached, readTTL())
	if err != nil {
		return nil, err
	}
	if hit {
		cached.Cached = true
		return &cached, nil
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if s.readCache.Get(ctx, key, &cached) {
		cached.Cached = true
		return &cached, nil
	}
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewFeedsListAction(page)

	feeds, err := action.GetFeedsList(ctx)
	if err != nil {
		logrus.Errorf("获取 Feeds 列表失败: %v", err)
		return nil, err
	}

	response := &FeedsListResponse{
		Feeds: feeds,
		Count: len(feeds),
	}

	response.ReadAt = time.Now()
	s.readCache.Put(key, response)
	return response, nil
}

func (s *XiaohongshuService) SearchFeeds(ctx context.Context, keyword string, filters ...xiaohongshu.FilterOption) (*FeedsListResponse, error) {
	keyword = strings.TrimSpace(keyword)
	if err := s.errIfRateLimited("search"); err != nil {
		return nil, err
	}
	// 冷却闸优先于 ensureLoggedIn：禁止再导航 search_result 触发扫码墙
	if err := s.errIfCaptchaCooldown("search_feeds"); err != nil {
		return nil, err
	}
	key := cacheKey(s, "SearchFeeds", []any{keyword, filters})
	var cached FeedsListResponse
	if s.searchCache.GetFor(ctx, key, &cached, searchCacheTTL()) {
		cached.Cached = true
		return &cached, nil
	}
	if err := s.searchSlot(false); err != nil {
		return nil, err
	}
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	key = cacheKey(s, "SearchFeeds", []any{keyword, filters})
	if s.searchCache.GetFor(ctx, key, &cached, searchCacheTTL()) {
		cached.Cached = true
		return &cached, nil
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if err := s.errIfRateLimited("search"); err != nil {
		return nil, err
	}
	if s.searchCache.GetFor(ctx, key, &cached, searchCacheTTL()) {
		cached.Cached = true
		return &cached, nil
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if err := s.searchSlot(true); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewSearchAction(page)

	feeds, err := action.Search(ctx, keyword, filters...)
	if err != nil {
		s.tripRateLimit("search", err)
		if errors.Is(err, xhserrors.ErrCaptcha) {
			s.tripCaptchaCooldown(err)
		}
		return nil, err
	}

	s.clearCaptchaCooldown()
	response := &FeedsListResponse{
		Feeds: feeds,
		Count: len(feeds),
	}

	response.ReadAt = time.Now()
	s.searchCache.PutFor(key, response, searchCacheTTL())
	return response, nil
}

// GetFeedDetail 获取Feed详情
func (s *XiaohongshuService) GetFeedDetail(ctx context.Context, feedID, xsecToken string, loadAllComments bool) (*FeedDetailResponse, error) {
	return s.GetFeedDetailWithConfig(ctx, feedID, xsecToken, loadAllComments, xiaohongshu.DefaultCommentLoadConfig())
}

// GetFeedDetailWithConfig 使用配置获取Feed详情
func (s *XiaohongshuService) GetFeedDetailWithConfig(ctx context.Context, feedID, xsecToken string, loadAllComments bool, config xiaohongshu.CommentLoadConfig) (*FeedDetailResponse, error) {
	// Rotating access tokens do not change a previously read note snapshot.
	var cached FeedDetailResponse
	key, hit, err := prepareRead(s, ctx, "GetFeedDetailWithConfig", []any{feedID, loadAllComments, config}, &s.detailCache, &cached, detailCacheTTL())
	if err != nil {
		return nil, err
	}
	if hit {
		cached.Cached = true
		return &cached, nil
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if s.detailCache.GetFor(ctx, key, &cached, detailCacheTTL()) {
		cached.Cached = true
		return &cached, nil
	}
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	return withTaskPage(s, ctx, false, func(page *rod.Page) (*FeedDetailResponse, error) {

		action := xiaohongshu.NewFeedDetailAction(page, ctx.Value(freshReadKey{}) == true)

		result, err := action.GetFeedDetailWithConfig(ctx, feedID, xsecToken, loadAllComments, config)
		if err != nil {
			s.tripCaptchaCooldown(err)
			return nil, err
		}

		response := &FeedDetailResponse{
			FeedID: feedID,
			Data:   result,
		}

		response.ReadAt = time.Now()
		s.detailCache.PutFor(key, response, detailCacheTTL())
		return response, nil
	})
}

// UserProfile 获取用户信息
func (s *XiaohongshuService) UserProfile(ctx context.Context, userID, xsecToken, tab string) (*UserProfileResponse, error) {
	parsed, err := xiaohongshu.ParseProfileTab(tab)
	if err != nil {
		s.tripCaptchaCooldown(err)
		return nil, err
	}
	var cached UserProfileResponse
	key, hit, err := prepareRead(s, ctx, "UserProfile", []any{userID, parsed}, &s.readCache, &cached, readTTL())
	if err != nil {
		return nil, err
	}
	if hit {
		cached.Cached = true
		return &cached, nil
	}

	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if s.readCache.Get(ctx, key, &cached) {
		cached.Cached = true
		return &cached, nil
	}
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewUserProfileAction(page)

	result, err := action.UserProfile(ctx, userID, xsecToken, parsed)
	if err != nil {
		s.tripCaptchaCooldown(err)
		return nil, err
	}
	response := &UserProfileResponse{
		UserBasicInfo: result.UserBasicInfo,
		Interactions:  result.Interactions,
		Feeds:         result.Feeds,
	}
	response.ReadAt = time.Now()
	s.readCache.Put(key, response)

	return response, nil

}

// requireWriteAllowed 写操作安全阀：仅 XHS_ALLOW_WRITE=1 时放行真发评/回评。
func requireWriteAllowed() error {
	if os.Getenv("XHS_ALLOW_WRITE") != "1" {
		return xhserrors.ErrWriteNotAllowed
	}
	return nil
}

// PostCommentToFeed 发表一级评论；未开 XHS_ALLOW_WRITE=1 时直接拒绝。
func (s *XiaohongshuService) postCommentToFeed(ctx context.Context, feedID, xsecToken, content string) (*PostCommentResponse, error) {
	if err := requireWriteAllowed(); err != nil {
		return nil, err
	}

	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	return withTaskPage(s, ctx, true, func(page *rod.Page) (*PostCommentResponse, error) {

		action := xiaohongshu.NewCommentFeedAction(page, os.Getenv("XHS_EXPECTED_USER_ID"))
		if err := action.PostComment(ctx, feedID, xsecToken, content); err != nil {
			return nil, err
		}

		return &PostCommentResponse{
			FeedID:  feedID,
			Success: true,
			Message: "评论发表成功",
		}, nil
	})
}

// LikeFeed 点赞笔记
func (s *XiaohongshuService) likeFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewLikeAction(page)
	if err := action.Like(ctx, feedID, xsecToken); err != nil {
		return nil, err
	}
	return &ActionResult{FeedID: feedID, Success: true, Message: "点赞成功或已点赞"}, nil
}

// UnlikeFeed 取消点赞笔记
func (s *XiaohongshuService) unlikeFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewLikeAction(page)
	if err := action.Unlike(ctx, feedID, xsecToken); err != nil {
		return nil, err
	}
	return &ActionResult{FeedID: feedID, Success: true, Message: "取消点赞成功或未点赞"}, nil
}

// FavoriteFeed 收藏笔记
func (s *XiaohongshuService) favoriteFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewFavoriteAction(page)
	if err := action.Favorite(ctx, feedID, xsecToken); err != nil {
		return nil, err
	}
	return &ActionResult{FeedID: feedID, Success: true, Message: "收藏成功或已收藏"}, nil
}

// UnfavoriteFeed 取消收藏笔记
func (s *XiaohongshuService) unfavoriteFeed(ctx context.Context, feedID, xsecToken string) (*ActionResult, error) {
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	action := xiaohongshu.NewFavoriteAction(page)
	if err := action.Unfavorite(ctx, feedID, xsecToken); err != nil {
		return nil, err
	}
	return &ActionResult{FeedID: feedID, Success: true, Message: "取消收藏成功或未收藏"}, nil
}

// ReplyCommentToFeed 回复指定评论；未开 XHS_ALLOW_WRITE=1 时直接拒绝。
func (s *XiaohongshuService) replyCommentToFeed(ctx context.Context, feedID, xsecToken, commentID, userID, content string) (*ReplyCommentResponse, error) {
	if err := requireWriteAllowed(); err != nil {
		return nil, err
	}

	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	return withTaskPage(s, ctx, true, func(page *rod.Page) (*ReplyCommentResponse, error) {

		action := xiaohongshu.NewCommentFeedAction(page, os.Getenv("XHS_EXPECTED_USER_ID"))

		if err := action.ReplyToComment(ctx, feedID, xsecToken, commentID, userID, content); err != nil {
			return nil, err
		}

		return &ReplyCommentResponse{
			FeedID:          feedID,
			TargetCommentID: commentID,
			TargetUserID:    userID,
			Success:         true,
			Message:         "评论回复成功",
		}, nil
	})
}

// GetUnreadCount 获取通知未读数
func (s *XiaohongshuService) GetUnreadCount(ctx context.Context) (*xiaohongshu.NotificationCount, error) {
	if err := s.readSlot(false); err != nil {
		return nil, err
	}
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return xiaohongshu.NewNotificationAction(page).UnreadCount(ctx)
}

// ListNotifications 获取指定分区的通知列表
func (s *XiaohongshuService) ListNotifications(ctx context.Context, tab string, limit int) (*xiaohongshu.NotificationList, error) {
	if err := s.readSlot(false); err != nil {
		return nil, err
	}
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	parsed, err := xiaohongshu.ParseNotificationTab(tab)
	if err != nil {
		s.tripCaptchaCooldown(err)
		return nil, err
	}

	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return xiaohongshu.NewNotificationAction(page).List(ctx, parsed, limit)
}

// LikeNotification 给通知里的评论点赞或取消点赞
func (s *XiaohongshuService) likeNotification(ctx context.Context, commentID string, unlike bool) (*xiaohongshu.NotificationLikeResult, error) {
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return xiaohongshu.NewNotificationAction(page).Like(ctx, commentID, unlike)
}

// ReplyNotification 在通知页就地回复评论
func (s *XiaohongshuService) replyNotification(ctx context.Context, commentID, content string) (*xiaohongshu.NotificationReplyResult, error) {
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return xiaohongshu.NewNotificationAction(page).Reply(ctx, commentID, content)
}

// ListDirectMessages 只读：刮取 www /chat 侧栏私信会话。
func (s *XiaohongshuService) ListDirectMessages(ctx context.Context, limit int) (*xiaohongshu.DirectMessageList, error) {
	if err := s.readSlot(false); err != nil {
		return nil, err
	}
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()
	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return xiaohongshu.NewDMAction(page).ListConversations(ctx, limit)
}

// ReplyDirectMessage 回复私信或仅打开会话。
// openOnly=true 时不发送、不要求 XHS_ALLOW_WRITE；真正发送需 XHS_ALLOW_WRITE=1。
func (s *XiaohongshuService) replyDirectMessage(ctx context.Context, conversationID, name, content string, openOnly bool) (*xiaohongshu.DirectMessageReplyResult, error) {
	if !openOnly {
		if err := requireWriteAllowed(); err != nil {
			return nil, err
		}
	}
	if openOnly {
		if err := s.readSlot(false); err != nil {
			return nil, err
		}
	}
	if err := s.ensureLoggedIn(ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if openOnly {
		if err := reserveReadSlot(s, ctx); err != nil {
			return nil, err
		}
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	b := newBrowser()
	defer b.Close()
	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return xiaohongshu.NewDMAction(page).ReplyOrOpen(ctx, conversationID, name, content, openOnly)
}

func newBrowser() *browser.Browser {
	return browser.NewBrowser(configs.IsHeadless(),
		browser.WithFingerprintSeed(configs.FingerprintSeed()),
		browser.WithProxy(configs.Proxy()),
	)
}

func saveCookies(page *rod.Page) error {
	if os.Getenv("XHS_EXPORT_COOKIES") != "1" {
		return nil
	}
	cks, err := page.Cookies([]string{"https://www.xiaohongshu.com", "https://creator.xiaohongshu.com"})
	if err != nil {
		return err
	}

	data, err := json.Marshal(cks)
	if err != nil {
		return err
	}

	cookieLoader := cookies.NewLoadCookie(cookies.GetCookiesFilePath())
	return cookieLoader.SaveCookies(data)
}

// withBrowserPage 执行需要浏览器页面的操作的通用函数
func withBrowserPage(ctx context.Context, fn func(*rod.Page) error) error {
	b := newBrowser()
	defer b.Close()

	page := b.NewPageContext(ctx)
	defer closeTaskPage(page)

	return fn(page)
}

// GetMyProfile 获取当前登录用户的个人信息
func (s *XiaohongshuService) GetMyProfile(ctx context.Context, tab string) (*UserProfileResponse, error) {
	parsed, err := xiaohongshu.ParseProfileTab(tab)
	if err != nil {
		s.tripCaptchaCooldown(err)
		return nil, err
	}
	var cached UserProfileResponse
	key, hit, err := prepareRead(s, ctx, "GetMyProfile", parsed, &s.readCache, &cached, readTTL())
	if err != nil {
		return nil, err
	}
	if hit {
		cached.Cached = true
		return &cached, nil
	}

	execution.Stage(ctx, "wait_browser", 30*time.Second)
	if err := s.browserMu.LockContext(ctx); err != nil {
		return nil, err
	}
	defer s.browserMu.Unlock()
	if s.readCache.Get(ctx, key, &cached) {
		cached.Cached = true
		return &cached, nil
	}
	if err := reserveReadSlot(s, ctx); err != nil {
		return nil, err
	}
	execution.Stage(ctx, "execute_browser", 120*time.Second)

	var result *xiaohongshu.UserProfileResponse

	err = withBrowserPage(ctx, func(page *rod.Page) error {
		// The verified main-site account is already known. Read its actual profile
		// directly, rather than opening explore again merely to click the sidebar.
		s.login.mu.Lock()
		userID := s.login.userID
		s.login.mu.Unlock()
		if userID == "" {
			return &runtimeError{Code: "ERR_ACCOUNT_UNCONFIRMED"}
		}
		action := xiaohongshu.NewUserProfileAction(page)
		result, err = action.UserProfile(ctx, userID, "", parsed)
		return err
	})

	if err != nil {
		s.tripCaptchaCooldown(err)
		return nil, err
	}

	response := &UserProfileResponse{
		UserBasicInfo: result.UserBasicInfo,
		Interactions:  result.Interactions,
		Feeds:         result.Feeds,
	}
	response.ReadAt = time.Now()
	s.readCache.Put(key, response)

	return response, nil
}

// 清理任务标签也有限时，不关闭宿主浏览器或其他标签。
func closeTaskPage(page *rod.Page) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := page.Context(ctx).Close(); err != nil {
		logrus.Warn("ERR_PAGE_CLEANUP: 任务标签关闭失败，保留浏览器会话")
	}
}
