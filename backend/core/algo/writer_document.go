package algo

import (
	"context"
	"encoding/json"
	"fmt"
)

type WriterDocumentSyncRequest struct {
	SourceDocument  json.RawMessage `json:"source_document"`
	RevisedDocument json.RawMessage `json:"revised_document"`
	ToolConfig      map[string]any  `json:"tool_config"`
}

type WriterDocumentSyncResponse struct {
	Success           bool            `json:"success"`
	Changed           bool            `json:"changed"`
	FeishuSynced      bool            `json:"feishu_synced"`
	PatchResult       json.RawMessage `json:"patch_result"`
	PersistedDocument json.RawMessage `json:"persisted_document"`
}

func SyncWriterDocument(
	ctx context.Context,
	req WriterDocumentSyncRequest,
) (*WriterDocumentSyncResponse, int, error) {
	action, status, err := InvokePluginAction(ctx, PluginActionInvokeRequest{
		PluginID: "writer-plugin",
		Action:   "sync_document",
		Phase:    "execute",
		Slot:     "draft_document",
		Arguments: map[string]any{
			"source_document":  req.SourceDocument,
			"revised_document": req.RevisedDocument,
		},
		ToolConfig: req.ToolConfig,
	})
	if err != nil {
		return nil, status, err
	}
	var response WriterDocumentSyncResponse
	if err := json.Unmarshal(action.Result, &response); err != nil {
		return nil, status, fmt.Errorf("decode sync_document action response: %w", err)
	}
	return &response, status, nil
}
