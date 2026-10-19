package browser

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestNavigationMetricsClassifyPathsWithoutSignedURLs(t *testing.T) {
	require.Equal(t, "detail", navigationCategory("https://www.xiaohongshu.com/explore/fixture?xsec_token=private-value"))
	require.Equal(t, "search", navigationCategory("https://www.xiaohongshu.com/search_result?keyword=private-value"))
	require.Equal(t, "profile", navigationCategory("https://www.xiaohongshu.com/user/profile/fixture"))
	require.Empty(t, navigationCategory("http://localhost:1234/fixture"))
	require.Empty(t, navigationCategory("https://www.xiaohongshu.com.example.com/explore/fixture"))
}
