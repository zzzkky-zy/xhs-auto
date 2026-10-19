package errors

import "errors"

var ErrNoFeeds = errors.New("没有捕获到 feeds 数据")
var ErrNoFeedDetail = errors.New("没有捕获到 feed 详情数据")

// ErrNotLoggedIn 登录墙 / 会话失效：搜帖探针与 check_login 共用。
// 调用方必须把此错误原样返回给客户端，禁止用 feeds=[] 假装成功。
var ErrNotLoggedIn = errors.New("ERR_NOT_LOGGED_IN: 小红书主站未登录或会话已失效；创作平台的登录界面不能替代主站身份。保存进度，在宿主私密接管所需站点登录后核对实际账号，保留浏览器 profile")

// ErrLoginWall 与 ErrNotLoggedIn 同义别名，便于语义阅读。
var ErrLoginWall = ErrNotLoggedIn

// ErrQRCodeUnavailable 二维码空白、「薯队长」占位或选择器失效。
var ErrQRCodeUnavailable = errors.New("ERR_QRCODE_UNAVAILABLE: 无法获取有效登录二维码（空白图/薯队长占位/DOM 变更），请重试或改用 login_with_sms")

// ErrSMSLoginFailed 短信登录失败或超时。
var ErrSMSLoginFailed = errors.New("ERR_SMS_LOGIN_FAILED: 短信登录失败或超时")

// ErrWriteNotAllowed 写操作安全阀：未设 XHS_ALLOW_WRITE=1 时拒绝发评/回评等写操作。
var ErrWriteNotAllowed = errors.New("写操作未开启: 请设置环境变量 XHS_ALLOW_WRITE=1 后重试")

// ErrSearchLoadFailed 搜帖页未完成加载（非登录墙、非真无结果）。
var ErrSearchLoadFailed = errors.New("ERR_SEARCH_LOAD_FAILED: 搜帖结果未加载完成（可能 CDP 页态竞态），请重试")

// ErrCaptcha 风控验证码页（如 /website-login/captcha、Security Verification）。
// 会话可能仍有效；禁止映射成 ErrNotLoggedIn，禁止因此写登录负缓存。
var ErrCaptcha = errors.New("ERR_CAPTCHA: 触发小红书安全验证/验证码页（会话可能仍有效）；保存进度，等待用户在宿主浏览器完成验证及冷却，保留原会话")

// 既未读到身份也未见登录墙，不得冒充已登录或掉线。
var ErrLoginStateUnknown = errors.New("ERR_LOGIN_STATE_UNKNOWN: 页面未提供可核实身份；保留会话，保存进度并检查页面或网络")

// Platform throttling is not evidence of either a valid or an expired session.
var ErrRateLimited = errors.New("ERR_RATE_LIMITED: 小红书返回300013/Too many requests；保存进度并停止相关请求，不重登、不清理会话、不自动探测解除时间")
