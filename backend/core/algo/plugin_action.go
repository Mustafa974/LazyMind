package algo

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"lazymind/core/common"
)

type PluginActionInvokeRequest struct {
	PluginID      string          `json:"plugin_id"`
	Action        string          `json:"action"`
	Phase         string          `json:"phase"`
	Slot          string          `json:"slot"`
	Artifact      json.RawMessage `json:"artifact,omitempty"`
	Arguments     map[string]any  `json:"arguments"`
	ArtifactStore string          `json:"artifact_store,omitempty"`
	LLMConfig     map[string]any  `json:"llm_config,omitempty"`
	ToolConfig    map[string]any  `json:"tool_config,omitempty"`
}

type PluginActionInvokeResponse struct {
	Result json.RawMessage `json:"result"`
}

func InvokePluginAction(
	ctx context.Context, req PluginActionInvokeRequest,
) (*PluginActionInvokeResponse, int, error) {
	var response PluginActionInvokeResponse
	err := common.ApiPost(
		ctx,
		common.JoinURL(common.ChatServiceEndpoint(), "/api/plugin/actions:invoke"),
		req, nil, &response, 2*time.Minute,
	)
	return &response, pluginActionHTTPStatus(err), err
}

func pluginActionHTTPStatus(err error) int {
	var httpErr *common.HTTPError
	if errors.As(err, &httpErr) {
		return httpErr.StatusCode
	}
	return 0
}
