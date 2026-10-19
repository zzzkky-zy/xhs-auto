//go:build fixture

package main

import (
	"context"
	"errors"
	"github.com/xpzouying/xiaohongshu-mcp/internal/fixturebrowser"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
	"github.com/go-rod/rod/lib/proto"
	"github.com/stretchr/testify/require"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
	"github.com/xpzouying/xiaohongshu-mcp/humanize"
)

func TestFixtureActionsUseFreshPagesAndPreserveFailedWrites(t *testing.T) {
	bin := fixturebrowser.Require(t)
	l := launcher.New().Bin(bin).Headless(true).UserDataDir(t.TempDir()).Leakless(false)
	control, err := l.Launch()
	require.NoError(t, err)
	defer l.Kill()
	t.Setenv("XHS_CDP_URL", control)
	t.Setenv("XHS_REUSE_RESEARCH_PAGE", "1")
	b := newBrowser()
	foreign := b.Rod().MustPage("about:blank")
	defer foreign.Close()
	s := testRuntime(t)
	seen := map[proto.TargetTargetID]bool{}
	for round := 0; round < 4; round++ {
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		id, err := withTaskPage(s, ctx, false, func(p *rod.Page) (*proto.TargetTargetID, error) {
			// Freeze/reactivate used to pass JS reads but hang here on the
			// second task. Exercise actual curved mouse movement and opening.
			require.NoError(t, p.SetDocumentContent(`<html><body style="margin:0"><div id="control" style="position:absolute;left:200px;top:400px;width:350px;height:40px"><span id="region">opening placeholder</span></div><script>window.clicks=0;document.querySelector("#control").onclick=()=>window.clicks++</script></body></html>`))
			require.NoError(t, p.Mouse.MoveTo(proto.Point{X: 550, Y: 420}))
			start := time.Now()
			err := humanize.Click(p.MustElement("#region"))
			t.Logf("round=%d opening elapsed=%v", round, time.Since(start))
			if err != nil {
				return nil, err
			}
			require.Equal(t, 1, p.MustEval(`() => window.clicks`).Int())
			return &p.TargetID, nil
		})
		cancel()
		require.NoError(t, err)
		require.False(t, seen[*id], "completed page must not be reused")
		seen[*id] = true
		_, err = (proto.TargetGetTargetInfo{TargetID: *id}).Call(b.Rod().Timeout(time.Second))
		require.Error(t, err, "only the completed owned page should close")
	}
	// A failed write may contain a draft or uncertain send evidence. Neither
	// completion retirement nor the janitor may close that preserved target.
	failure := errors.New("fixture failed write; preserve its page")
	// Proven pre-submit failures save drafts and release only owned pages. Three
	// consecutive failures must not exhaust the two uncertain-page allowance.
	for round := 0; round < 3; round++ {
		var unsubmittedID proto.TargetTargetID
		_, err = withTaskPage(s, execution.TrackSubmission(context.Background()), true, func(p *rod.Page) (*bool, error) {
			unsubmittedID = p.TargetID
			require.NoError(t, p.SetDocumentContent(`<textarea>保留的实际草稿</textarea>`))
			return nil, failure
		})
		require.ErrorIs(t, err, failure)
		require.NotContains(t, s.pages.preserved, unsubmittedID)
		_, err = (proto.TargetGetTargetInfo{TargetID: unsubmittedID}).Call(b.Rod().Timeout(time.Second))
		require.Error(t, err)
	}
	diagnostic, err := os.ReadFile(filepath.Join(runtimeDir(), "unsubmitted-page-diagnostics.json"))
	require.NoError(t, err)
	require.Contains(t, string(diagnostic), "保留的实际草稿")
	var failedID proto.TargetTargetID
	_, err = withTaskPage(s, context.Background(), true, func(p *rod.Page) (*bool, error) {
		failedID = p.TargetID
		return nil, failure
	})
	require.ErrorIs(t, err, failure)
	require.Contains(t, s.pages.preserved, failedID)
	s.releaseExpiredResources(context.Background())
	_, err = (proto.TargetGetTargetInfo{TargetID: failedID}).Call(b.Rod().Timeout(time.Second))
	require.NoError(t, err)
	_, err = (proto.TargetGetTargetInfo{TargetID: foreign.TargetID}).Call(b.Rod().Timeout(time.Second))
	require.NoError(t, err)
	// This fixture owns the failed target and has no external submission.
	_, _ = (proto.TargetCloseTarget{TargetID: failedID}).Call(b.Rod().Timeout(time.Second))
}
