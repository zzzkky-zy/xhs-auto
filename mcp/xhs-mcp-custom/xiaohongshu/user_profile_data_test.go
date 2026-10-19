package xiaohongshu

import (
	"testing"

	"github.com/stretchr/testify/require"
)

func TestDecodeUserProfileRequiresLoadedIdentity(t *testing.T) {
	_, err := decodeUserProfileData(`{"basicInfo":{},"interactions":null}`, `{"notes":[[],[],[]],"index":0,"query":"note"}`, TabNotes)
	require.ErrorContains(t, err, "ERR_PROFILE_DATA_UNAVAILABLE")
}

func TestDecodeUserProfileAllowsAnAccountWithNoPosts(t *testing.T) {
	result, err := decodeUserProfileData(`{"basicInfo":{"nickname":"Loaded account"},"interactions":[]}`, `{"notes":[[],[],[]],"index":0,"query":"note"}`, TabNotes)
	require.NoError(t, err)
	require.Equal(t, "Loaded account", result.UserBasicInfo.Nickname)
	require.Empty(t, result.Feeds)
}
