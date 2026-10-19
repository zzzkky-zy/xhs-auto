//go:build fixture

package xiaohongshu

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

func TestFixturePreparedDraftInput(t *testing.T) {
	b := commentFixtureBrowser(t)
	// Exercise long Unicode drafts through native paragraph input.
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	text := strings.Repeat("中文、标点和 emoji 🦆 都保留。", 12) + "\n\n下一段保留空行。\n最后一行"
	for _, editor := range []string{`<textarea id="draft"></textarea>`, `<div id="draft" contenteditable="true" style="white-space:pre-wrap"></div>`, `<div id="draft" contenteditable="true"></div>`, `<div id="draft" contenteditable="true"><p><br></p></div>`} {
		p := commentFixturePage(t, b, editor)
		e := p.MustElement("#draft")
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		start := time.Now()
		err := humanize.FillDraft(ctx, e, text)
		if err != nil {
			actual, _ := e.Eval(`() => typeof this.value === "string" ? this.value : this.innerText`)
			t.Fatalf("editor=%s err=%v actual=%q expected=%q", editor, err, actual.Value.Str(), text)
		}
		require.Less(t, time.Since(start), 4*time.Second)
		actual, readErr := e.Eval(`() => (typeof this.value === "string" ? this.value : this.textContent).replace(/\s/g, "")`)
		require.NoError(t, readErr)
		require.Equal(t, strings.Join(strings.Fields(text), ""), actual.Value.Str())
		t.Logf("prepared text: %d bytes, native input and verification %s", len(text), time.Since(start))
		require.NoError(t, humanize.FillDraft(ctx, e, text)) // no duplicate append
		cancel()
	}
	t.Run("title and draft already present are preserved", func(t *testing.T) {
		p := commentFixturePage(t, b, `<input id="draft" value="用户原稿">`)
		require.ErrorContains(t, humanize.FillDraft(context.Background(), p.MustElement("#draft"), "另一稿"), "ERR_INPUT_NOT_EMPTY")
		v, err := p.Eval(`() => document.querySelector('#draft').value`)
		require.NoError(t, err)
		require.Equal(t, "用户原稿", v.Value.Str())
	})
	t.Run("clipped content is never accepted", func(t *testing.T) {
		p := commentFixturePage(t, b, `<input id="draft" maxlength="2">`)
		require.ErrorContains(t, humanize.FillDraft(context.Background(), p.MustElement("#draft"), "超过两字"), "ERR_INPUT_MISMATCH")
	})
	t.Run("canceled input does not touch editor", func(t *testing.T) {
		p := commentFixturePage(t, b, `<textarea id="draft"></textarea>`)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		require.ErrorIs(t, humanize.FillDraft(ctx, p.MustElement("#draft"), text), context.Canceled)
		v, err := p.Eval(`() => document.querySelector('#draft').value`)
		require.NoError(t, err)
		require.Empty(t, v.Value.Str())
	})
}

func TestFixturePreparedInputEventsWithDefaultTiming(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(humanize.DefaultProvider{})
	p := commentFixturePage(t, b, `<textarea id="draft" oninput="window.inputs=(window.inputs||0)+1" onkeydown="if(event.key==='Enter'&&!event.shiftKey)window.submits=(window.submits||0)+1"></textarea>`)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	const text = "你好🦆\n下一行"
	require.NoError(t, humanize.FillDraft(ctx, p.MustElement("#draft"), text))
	actual, err := p.Eval(`() => [document.querySelector('#draft').value,window.inputs,window.submits||0]`)
	require.NoError(t, err)
	require.Equal(t, text, actual.Value.Get("0").Str())
	require.Equal(t, 3, actual.Value.Get("1").Int(), "two native paragraph inputs and one line break")
	require.Zero(t, actual.Value.Get("2").Int(), "typing a new line must not invoke an Enter-to-submit shortcut")
}

// The live creator editor ignores Shift+Enter. Preserve all paragraphs without
// weakening verification or changing comment Enter-to-send behavior.
func TestFixturePostDraftParagraphs(t *testing.T) {
	b := commentFixtureBrowser(t)
	humanize.SetProvider(fixtureCommentTiming{})
	defer humanize.SetProvider(humanize.DefaultProvider{})
	p := commentFixturePage(t, b, `<div id="draft" contenteditable="true"><p><br></p></div><script>document.querySelector('#draft').addEventListener('keydown',e=>{if(e.key==='Enter' && e.shiftKey)e.preventDefault()})</script>`)
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()
	text := "第一段保留🦆\n\n第二段\n第三行"
	require.NoError(t, humanize.FillPostDraft(ctx, p.MustElement("#draft"), text))
	require.NoError(t, humanize.FillPostDraft(ctx, p.MustElement("#draft"), text))
	require.ErrorContains(t, humanize.FillPostDraft(ctx, p.MustElement("#draft"), "另一个稿件"), "ERR_INPUT_NOT_EMPTY")
}
