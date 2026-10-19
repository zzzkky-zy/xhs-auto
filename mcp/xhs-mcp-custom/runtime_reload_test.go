package main

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/require"
)

func TestReloadDrainRefusesActiveRequestAndClosesAdmissionWhenIdle(t *testing.T) {
	gin.SetMode(gin.ReleaseMode)
	app := &AppServer{}
	router := gin.New()
	router.Use(app.admissionMiddleware())
	router.POST("/runtime/drain", app.drainHandler)
	dispatched := 0
	router.POST("/action", func(c *gin.Context) { dispatched++; c.Status(200) })
	call := func(path string) int {
		out := httptest.NewRecorder()
		router.ServeHTTP(out, httptest.NewRequest(http.MethodPost, path, nil))
		return out.Code
	}
	app.activeRequests = 1
	require.Equal(t, 409, call("/runtime/drain"))
	require.False(t, app.draining)
	app.activeRequests = 0
	require.Equal(t, 200, call("/action"))
	require.Equal(t, 200, call("/runtime/drain"))
	require.Equal(t, 503, call("/action"))
	require.Equal(t, 1, dispatched)
}
