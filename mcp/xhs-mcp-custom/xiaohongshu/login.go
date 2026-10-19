package xiaohongshu

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/input"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	xhserrors "github.com/xpzouying/xiaohongshu-mcp/errors"
)

type LoginAction struct {
	page *rod.Page
}

func NewLogin(page *rod.Page) *LoginAction {
	return &LoginAction{page: page}
}

// CheckLoginStatus 不搜帖；首页必须有实际登录用户 ID。
func (a *LoginAction) CheckLoginStatus(ctx context.Context) (bool, error) {
	pp := a.page.Context(ctx).Timeout(30 * time.Second)
	if err := pp.Navigate(webBaseURL() + "/explore"); err != nil {
		return false, err
	}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if err := ctx.Err(); err != nil {
			return false, err
		}
		wall, reason := detectLoginWall(pp)
		if wall {
			return false, errFromLoginWall(reason)
		}
		if reason == "eval_failed" {
			return false, xhserrors.ErrLoginStateUnknown
		}
		if user, err := a.CurrentUser(ctx); err == nil && user.UserID != "" {
			return true, nil
		}
		select {
		case <-ctx.Done():
			return false, ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
	return false, xhserrors.ErrLoginStateUnknown
}

// captchaWallReason detectLoginWall 返回的验证码墙 reason（须先于 login_path）。
const captchaWallReason = "captcha"
const rateLimitWallReason = "rate_limit"

// isCaptchaReason 是否为验证码/安全验证墙（非掉线）。
func isCaptchaReason(reason string) bool {
	return reason == captchaWallReason
}

// classifyHrefForWall 纯 URL 分类（单测用）：captcha 必须先于 login_path。
// /website-login/captcha 含 "login" 子串，旧逻辑会误判 login_path。
func classifyHrefForWall(href string) string {
	u, err := url.Parse(href)
	if err != nil {
		return ""
	}
	lower := strings.ToLower(u.Path)
	if strings.TrimSuffix(lower, "/") == "/website-login/error" &&
		(u.Query().Get("error_code") == "300013" || strings.Contains(strings.ToLower(u.Query().Get("error_msg")), "too many requests")) {
		return rateLimitWallReason
	}
	if strings.Contains(lower, "captcha") || strings.Contains(lower, "website-login/captcha") {
		return captchaWallReason
	}
	if lower == "/login" || strings.HasPrefix(lower, "/login/") || strings.Contains(lower, "/passport") || lower == "/website-login" {
		return "login_path"
	}
	return ""
}

// classifyBodyForCaptcha 正文里的 Security Verification / 安全验证等（单测用）。
func classifyBodyForCaptcha(bodyText string) bool {
	if bodyText == "" {
		return false
	}
	patterns := []string{
		"Security Verification",
		"security verification",
		"安全验证",
		"请完成验证",
		"滑动验证",
	}
	for _, p := range patterns {
		if strings.Contains(bodyText, p) {
			return true
		}
	}
	return false
}

// errFromLoginWall 把 detectLoginWall 的 reason 映射为对外错误。
// captcha → ErrCaptcha（禁止 ErrNotLoggedIn）；其它墙 → ErrNotLoggedIn。
func errFromLoginWall(reason string) error {
	if reason == rateLimitWallReason {
		return xhserrors.ErrRateLimited
	}
	if isCaptchaReason(reason) {
		return xhserrors.ErrCaptcha
	}
	return xhserrors.ErrNotLoggedIn
}

// detectLoginWall 检测验证码墙 / 登录弹层 / 登录页 / 空白搜帖墙。
// 验证码（captcha）必须先于 login_path：URL /website-login/captcha 含 "login" 子串。
func detectLoginWall(page *rod.Page) (bool, string) {
	res, err := page.Eval(`() => {
		const text = (document.body && document.body.innerText) ? document.body.innerText : "";
		const hasLoginModal = !!(
			document.querySelector('.login-container') ||
			document.querySelector('.login-box') ||
			document.querySelector('[class*="login-modal"]') ||
			document.querySelector('.qrcode-img') ||
			document.querySelector('img[class*="qrcode"]')
		);
		const cta = /登录后推荐|扫码登录|手机号登录|验证码登录|请先登录|登录后查看|登录后搜索/.test(text);
		const shuCaptain = text.includes("薯队长");
		const href = location.pathname || "";
		const url = new URL(location.href);
		const onRateLimitURL = /^\/website-login\/error\/?$/i.test(href) &&
			(url.searchParams.get('error_code') === '300013' || /too many requests/i.test(url.searchParams.get('error_msg') || ''));
		const onRateLimitPage = /安全限制|too many requests/i.test(document.title || '') &&
			/300013/.test(text) && /too many requests/i.test(text);
		// captcha / Security Verification 先于 login_path（website-login/captcha 含 login）
		const onCaptcha = /captcha/i.test(href) || /website-login\/captcha/i.test(href) ||
			/Security Verification|安全验证|请完成验证|滑动验证/i.test(text);
		const onLoginPath = /^\/(login|passport)(\/|$)|^\/website-login$/.test(href);
		// 搜索页被墙时常有明显登录 CTA 且无 feeds
		const feeds = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.search && window.__INITIAL_STATE__.search.feeds;
		const feedsData = feeds ? (feeds.value !== undefined ? feeds.value : feeds._value) : null;
		const feedsEmpty = !feedsData || (Array.isArray(feedsData) && feedsData.length === 0);
		const user = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user;
		const info = user && user.userInfo && (user.userInfo.value !== undefined ? user.userInfo.value : user.userInfo);
		const guest = !info || info.guest === true || !info.userId && !info.user_id;

		if (onRateLimitURL || onRateLimitPage) return {wall:true, reason:"rate_limit"};
		if (onCaptcha) return {wall:true, reason:"captcha"};
		if (onLoginPath) return {wall:true, reason:"login_path"};
		if (hasLoginModal && (cta || shuCaptain || guest)) return {wall:true, reason:"login_modal"};
		if (cta && feedsEmpty && guest) return {wall:true, reason:"search_cta_wall"};
		if (shuCaptain && hasLoginModal) return {wall:true, reason:"shu_captain"};
		return {wall:false, reason:""};
	}`)
	if err != nil {
		return false, "eval_failed"
	}
	m := res.Value.Map()
	if m == nil {
		return false, ""
	}
	wall := m["wall"].Bool()
	reason := m["reason"].Str()
	return wall, reason
}

func isGuestUser(page *rod.Page) bool {
	res, err := page.Eval(`() => {
		const u = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user;
		const info = u && u.userInfo && (u.userInfo.value !== undefined ? u.userInfo.value : u.userInfo);
		if (!info) return true;
		if (info.guest === true) return true;
		if (!info.userId && !info.user_id) return true;
		return false;
	}`)
	if err != nil {
		return true
	}
	return res.Value.Bool()
}

func searchHasFeedsOrReady(page *rod.Page) bool {
	res, err := page.Eval(`() => {
		const f = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.search && window.__INITIAL_STATE__.search.feeds;
		if (!f) return false;
		const v = f.value !== undefined ? f.value : f._value;
		return Array.isArray(v) && v.length > 0;
	}`)
	if err != nil {
		return false
	}
	return res.Value.Bool()
}

// CurrentUser 当前登录用户的基础信息。
type CurrentUser struct {
	Nickname string `json:"nickname"`
	UserID   string `json:"userId"`
}

// CurrentUser 从当前页面的 __INITIAL_STATE__ 读取登录用户信息。
func (a *LoginAction) CurrentUser(ctx context.Context) (*CurrentUser, error) {
	pp := a.page.Context(ctx).Timeout(10 * time.Second)

	res, err := pp.Eval(`() => {
		const u = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user;
		const ref = u && u.userInfo;
		const info = ref && (ref.value !== undefined ? ref.value : ref._value !== undefined ? ref._value : ref);
		if (!info || info.guest) return "";
		return JSON.stringify({nickname: info.nickname, userId: info.userId || info.user_id});
	}`)
	if err != nil {
		return nil, errors.Wrap(err, "read current user state failed")
	}

	raw := res.Value.String()
	if raw == "" {
		return nil, errors.New("current user not found in page state")
	}

	var user CurrentUser
	if err := json.Unmarshal([]byte(raw), &user); err != nil {
		return nil, errors.Wrap(err, "unmarshal current user failed")
	}

	return &user, nil
}

func (a *LoginAction) Login(ctx context.Context) error {
	pp := a.page.Context(ctx)

	pp.MustNavigate("https://www.xiaohongshu.com/explore").MustWaitLoad()
	time.Sleep(2 * time.Second)

	if ok, err := a.CheckLoginStatus(ctx); err == nil && ok {
		return nil
	}

	pp.MustElement(".main-container .user .link-wrapper .channel")
	return nil
}

// qrcodeSelectors 候选选择器：不挂死单一 .qrcode-img。
var qrcodeSelectors = []string{
	`.login-container .qrcode-img`,
	`.login-box .qrcode-img`,
	`img.qrcode-img`,
	`.qrcode-img`,
	`img[class*="qrcode"]`,
	`.login-container img[src*="data:image"]`,
	`.login-container img[src*="http"]`,
}

// FetchQrcodeImage 获取登录二维码；空白/「薯队长」会重试换入口。
// 返回 (imgDataURL, alreadyLoggedIn, error)。
func (a *LoginAction) FetchQrcodeImage(ctx context.Context) (string, bool, error) {
	pp := a.page.Context(ctx).Timeout(90 * time.Second)

	entries := []string{
		"https://www.xiaohongshu.com/explore",
		"https://www.xiaohongshu.com/login",
	}

	var lastErr error
	for attempt, entry := range entries {
		select {
		case <-ctx.Done():
			return "", false, ctx.Err()
		default:
		}

		logrus.Infof("FetchQrcodeImage attempt %d entry=%s", attempt+1, entry)
		if err := pp.Navigate(entry); err != nil {
			lastErr = err
			continue
		}
		_ = pp.WaitLoad()
		time.Sleep(2 * time.Second)

		// 已登录则直接返回
		if wall, _ := detectLoginWall(pp); !wall {
			if ok, err := a.quickLoggedInUI(pp); err == nil && ok {
				return "", true, nil
			}
			if !isGuestUser(pp) {
				return "", true, nil
			}
		}

		// 尝试点开登录入口
		_ = clickLoginEntry(pp)
		time.Sleep(1500 * time.Millisecond)

		src, err := a.readValidQrcodeSrc(pp)
		if err != nil {
			lastErr = err
			logrus.Warnf("qrcode attempt %d failed: %v", attempt+1, err)
			// 刷新登录弹层再试一次
			_ = clickLoginEntry(pp)
			time.Sleep(2 * time.Second)
			src, err = a.readValidQrcodeSrc(pp)
			if err != nil {
				lastErr = err
				continue
			}
		}
		return src, false, nil
	}

	if lastErr != nil {
		return "", false, errors.Wrap(lastErr, xhserrors.ErrQRCodeUnavailable.Error())
	}
	return "", false, xhserrors.ErrQRCodeUnavailable
}

func (a *LoginAction) quickLoggedInUI(pp *rod.Page) (bool, error) {
	exists, _, err := pp.Has(`.main-container .user .link-wrapper .channel`)
	if err != nil {
		return false, err
	}
	return exists, nil
}

func clickLoginEntry(page *rod.Page) error {
	// 尝试常见「登录」按钮文案
	clicked, err := page.Eval(`() => {
		const texts = ["登录", "立即登录", "手机号登录"];
		const nodes = Array.from(document.querySelectorAll("button, a, span, div"));
		for (const t of texts) {
			const el = nodes.find(n => (n.innerText || "").trim() === t && n.offsetParent !== null);
			if (el) { el.click(); return true; }
		}
		// 右上角用户区
		const user = document.querySelector(".main-container .user .link-wrapper");
		if (user) { user.click(); return true; }
		return false;
	}`)
	if err != nil {
		return err
	}
	if !clicked.Value.Bool() {
		return fmt.Errorf("no login entry clicked")
	}
	return nil
}

func (a *LoginAction) readValidQrcodeSrc(pp *rod.Page) (string, error) {
	for _, sel := range qrcodeSelectors {
		el, err := pp.Timeout(5 * time.Second).Element(sel)
		if err != nil || el == nil {
			continue
		}
		src, err := el.Attribute("src")
		if err != nil || src == nil || *src == "" {
			continue
		}
		s := *src
		if isBlankOrPlaceholderQR(pp, el, s) {
			logrus.Warnf("qrcode selector %s 命中但为空白/薯队长占位", sel)
			continue
		}
		return s, nil
	}

	// 兜底：任意可见二维码 canvas → 不支持则返回错误
	return "", xhserrors.ErrQRCodeUnavailable
}

// isBlankOrPlaceholderQR 检测空白图或「薯队长」占位。
func isBlankOrPlaceholderQR(page *rod.Page, el *rod.Element, src string) bool {
	if strings.Contains(src, "data:image") && len(src) < 200 {
		return true // 极短 data-url 多半是空图
	}
	// 「薯队长」占位文案
	res, err := page.Eval(`() => (document.body && document.body.innerText || "").includes("薯队长")`)
	nearShu := err == nil && res.Value.Bool()
	shape, err := el.Shape()
	tiny := false
	if err == nil && len(shape.Quads) > 0 {
		q := shape.Quads[0]
		w := q[2] - q[0]
		h := q[5] - q[1]
		tiny = w < 40 || h < 40
	}
	if nearShu && tiny {
		return true
	}
	if tiny {
		return true
	}
	// 极短 http 占位图路径
	if len(src) < 40 && !strings.HasPrefix(src, "data:image") {
		return true
	}
	return false
}

func (a *LoginAction) WaitForLogin(ctx context.Context) bool {
	pp := a.page.Context(ctx)
	ticker := time.NewTicker(800 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return false
		case <-ticker.C:
			wall, _ := detectLoginWall(pp)
			if wall {
				continue
			}
			if exists, _, _ := pp.Has(".main-container .user .link-wrapper .channel"); exists {
				return true
			}
			if !isGuestUser(pp) {
				return true
			}
		}
	}
}

// LoginWithSMS 短信验证码登录。
// phone: 手机号；code: 短信验证码（由聊天/工具参数传入，不接自动接码平台）。
// 若 code 为空，则只触发发送验证码并返回需要 code 的提示错误。
func (a *LoginAction) LoginWithSMS(ctx context.Context, phone, code string) error {
	phone = strings.TrimSpace(phone)
	code = strings.TrimSpace(code)
	if phone == "" {
		return fmt.Errorf("%w: 缺少 phone", xhserrors.ErrSMSLoginFailed)
	}

	pp := a.page.Context(ctx).Timeout(120 * time.Second)

	if err := pp.Navigate("https://www.xiaohongshu.com/explore"); err != nil {
		return errors.Wrap(err, "navigate for sms login")
	}
	_ = pp.WaitLoad()
	time.Sleep(2 * time.Second)

	// 已登录则跳过
	if wall, _ := detectLoginWall(pp); !wall && !isGuestUser(pp) {
		return nil
	}

	_ = clickLoginEntry(pp)
	time.Sleep(1500 * time.Millisecond)

	if err := switchToSMSLogin(pp); err != nil {
		logrus.Warnf("切换短信登录 Tab 可能失败，继续尝试: %v", err)
	}
	time.Sleep(800 * time.Millisecond)

	if err := fillPhoneAndMaybeSendCode(pp, phone, code == ""); err != nil {
		return errors.Wrap(err, xhserrors.ErrSMSLoginFailed.Error())
	}

	if code == "" {
		return fmt.Errorf("%w: 验证码已尝试发送，请把短信码通过 login_with_sms 的 code 参数再次调用", xhserrors.ErrSMSLoginFailed)
	}

	if err := fillSMSCodeAndSubmit(pp, code); err != nil {
		return errors.Wrap(err, xhserrors.ErrSMSLoginFailed.Error())
	}

	// 等待登录成功
	waitCtx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	if !a.WaitForLogin(waitCtx) {
		return fmt.Errorf("%w: 提交验证码后未在超时内检测到登录成功", xhserrors.ErrSMSLoginFailed)
	}
	return nil
}

func switchToSMSLogin(page *rod.Page) error {
	res, err := page.Eval(`() => {
		const labels = ["密码登录", "验证码登录", "手机号登录", "短信登录"];
		// 优先点「验证码登录」/「手机号登录」
		const prefer = ["验证码登录", "手机号登录", "短信登录"];
		const nodes = Array.from(document.querySelectorAll("div, span, button, a, li"));
		for (const t of prefer) {
			const el = nodes.find(n => (n.innerText || "").trim() === t && n.offsetParent !== null);
			if (el) { el.click(); return t; }
		}
		// 若当前是二维码态，找切换图标/文案
		const switchEl = nodes.find(n => /手机号|验证码/.test((n.innerText || "").trim()) && n.offsetParent !== null);
		if (switchEl) { switchEl.click(); return switchEl.innerText.trim(); }
		return "";
	}`)
	if err != nil {
		return err
	}
	if res.Value.Str() == "" {
		return fmt.Errorf("未找到短信登录入口")
	}
	logrus.Infof("已切换到短信登录: %s", res.Value.Str())
	return nil
}

func fillPhoneAndMaybeSendCode(page *rod.Page, phone string, sendCode bool) error {
	// 找手机号输入框
	inputEl, err := findPhoneInput(page)
	if err != nil {
		return err
	}
	_ = inputEl.SelectAllText()
	if err := inputEl.Input(phone); err != nil {
		_ = inputEl.Focus()
		_ = page.InsertText(phone)
	}
	time.Sleep(400 * time.Millisecond)

	if !sendCode {
		return nil
	}

	clicked, err := page.Eval(`() => {
		const texts = ["获取验证码", "发送验证码", "获取短信验证码"];
		const nodes = Array.from(document.querySelectorAll("button, a, span, div"));
		for (const t of texts) {
			const el = nodes.find(n => (n.innerText || "").trim() === t && n.offsetParent !== null);
			if (el) { el.click(); return t; }
		}
		return "";
	}`)
	if err != nil {
		return err
	}
	if clicked.Value.Str() == "" {
		return fmt.Errorf("未找到「获取验证码」按钮")
	}
	logrus.Infof("已点击发送验证码: %s", clicked.Value.Str())
	time.Sleep(1 * time.Second)
	return nil
}

func findPhoneInput(page *rod.Page) (*rod.Element, error) {
	selectors := []string{
		`input[placeholder*="手机号"]`,
		`input[placeholder*="手机"]`,
		`input[type="tel"]`,
		`.login-container input[type="text"]`,
		`input[name="phone"]`,
	}
	for _, sel := range selectors {
		el, err := page.Timeout(3 * time.Second).Element(sel)
		if err == nil && el != nil {
			return el, nil
		}
	}
	return nil, fmt.Errorf("未找到手机号输入框")
}

func fillSMSCodeAndSubmit(page *rod.Page, code string) error {
	selectors := []string{
		`input[placeholder*="验证码"]`,
		`input[placeholder*="短信"]`,
		`input[name="code"]`,
		`input[name="sms"]`,
	}
	var codeEl *rod.Element
	var err error
	for _, sel := range selectors {
		codeEl, err = page.Timeout(3 * time.Second).Element(sel)
		if err == nil && codeEl != nil {
			break
		}
		codeEl = nil
	}
	if codeEl == nil {
		return fmt.Errorf("未找到验证码输入框")
	}
	_ = codeEl.SelectAllText()
	if err := codeEl.Input(code); err != nil {
		_ = codeEl.Focus()
		_ = page.InsertText(code)
	}
	time.Sleep(400 * time.Millisecond)

	submitted, err := page.Eval(`() => {
		const texts = ["登录", "确认", "提交", "立即登录"];
		const nodes = Array.from(document.querySelectorAll("button, a"));
		for (const t of texts) {
			const el = nodes.find(n => (n.innerText || "").trim() === t && n.offsetParent !== null);
			if (el) { el.click(); return t; }
		}
		return "";
	}`)
	if err != nil {
		return err
	}
	if submitted.Value.Str() == "" {
		// 尝试 Enter
		_ = codeEl.Focus()
		_ = page.Keyboard.Press(input.Enter)
		logrus.Info("未找到登录按钮，已尝试 Enter 提交")
	} else {
		logrus.Infof("已点击提交: %s", submitted.Value.Str())
	}
	return nil
}
