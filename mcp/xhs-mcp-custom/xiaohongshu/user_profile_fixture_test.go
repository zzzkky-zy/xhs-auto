//go:build fixture

package xiaohongshu

import (
	"fmt"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestFixtureProfileReadsPlainAndReactiveState(t *testing.T) {
	b := commentFixtureBrowser(t)
	for _, wrap := range []string{"%s", "{value:%s}", "{_value:%s}"} {
		t.Run(wrap, func(t *testing.T) {
			profile := fmt.Sprintf(wrap, `{basicInfo:{nickname:"fixture account"},interactions:[]}`)
			notes := fmt.Sprintf(wrap, `[[{id:"fixture-note"}],[],[]]`)
			active := fmt.Sprintf(wrap, `{index:0,query:"note"}`)
			page := commentFixturePage(t, b, fmt.Sprintf(`<script>window.__INITIAL_STATE__={user:{userPageData:%s,notes:%s,activeTab:%s}};</script>`, profile, notes, active))
			result, err := NewUserProfileAction(page).extractUserProfileData(page, TabNotes)
			require.NoError(t, err)
			require.Equal(t, "fixture account", result.UserBasicInfo.Nickname)
			require.Len(t, result.Feeds, 1)
		})
	}
}
