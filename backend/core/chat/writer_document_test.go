package chat

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"lazymind/core/common/orm"
)

// TestWriterSyncStatus_BadRequest maps 400 and 422 to 400.
func TestWriterSyncStatus_BadRequest(t *testing.T) {
	if got := writerSyncStatus(http.StatusBadRequest); got != http.StatusBadRequest {
		t.Fatalf("status 400: got %d, want %d", got, http.StatusBadRequest)
	}
	if got := writerSyncStatus(http.StatusUnprocessableEntity); got != http.StatusBadRequest {
		t.Fatalf("status 422: got %d, want %d", got, http.StatusBadRequest)
	}
}

// TestWriterSyncStatus_AuthErrors passes through 401, 403, 409.
func TestWriterSyncStatus_AuthErrors(t *testing.T) {
	if got := writerSyncStatus(http.StatusUnauthorized); got != http.StatusUnauthorized {
		t.Fatalf("got %d, want %d", got, http.StatusUnauthorized)
	}
	if got := writerSyncStatus(http.StatusForbidden); got != http.StatusForbidden {
		t.Fatalf("got %d, want %d", got, http.StatusForbidden)
	}
	if got := writerSyncStatus(http.StatusConflict); got != http.StatusConflict {
		t.Fatalf("got %d, want %d", got, http.StatusConflict)
	}
}

// TestWriterSyncStatus_Default maps unrecognized statuses to 502.
func TestWriterSyncStatus_Default(t *testing.T) {
	if got := writerSyncStatus(http.StatusOK); got != http.StatusBadGateway {
		t.Fatalf("status 200: got %d, want %d", got, http.StatusBadGateway)
	}
	if got := writerSyncStatus(http.StatusInternalServerError); got != http.StatusBadGateway {
		t.Fatalf("status 500: got %d, want %d", got, http.StatusBadGateway)
	}
	if got := writerSyncStatus(http.StatusNotFound); got != http.StatusBadGateway {
		t.Fatalf("status 404: got %d, want %d", got, http.StatusBadGateway)
	}
	if got := writerSyncStatus(999); got != http.StatusBadGateway {
		t.Fatalf("unknown: got %d, want %d", got, http.StatusBadGateway)
	}
}

func TestLoadWriterWriteBackBaseline_UsesCurrentRevisionSnapshot(t *testing.T) {
	db := orm.MigrateTestDB(t, &orm.PluginSlotRevision{})
	baselineValue := json.RawMessage(`{"data":{"document_id":"feishu-doc","title":"baseline","provider_binding":{"provider":"feishu","document_id":"feishu-doc"}}}`)
	seedWriterRevision(t, db, "draft-2", "draft_document", 2, true, "provider_sync", baselineValue)

	baseline, err := loadWriterWriteBackBaseline(context.Background(), db.DB, "session", 2)
	if err != nil {
		t.Fatalf("load baseline: %v", err)
	}
	if baseline.Revision.ID != "draft-2" {
		t.Fatalf("baseline id = %q, want draft-2", baseline.Revision.ID)
	}
	if !json.Valid(baseline.Value) || string(baseline.Value) != string(baselineValue) {
		t.Fatalf("baseline value = %s, want %s", baseline.Value, baselineValue)
	}
}

func TestLoadWriterWriteBackBaseline_RejectsMissingCurrentSnapshot(t *testing.T) {
	db := orm.MigrateTestDB(t, &orm.PluginSlotRevision{})
	seedWriterRevision(t, db, "draft-1", "draft_document", 1, false, "provider_sync", json.RawMessage(`{"old":true}`))
	seedWriterRevision(t, db, "draft-2", "draft_document", 2, true, "human", nil)

	if _, err := loadWriterWriteBackBaseline(context.Background(), db.DB, "session", 2); err == nil {
		t.Fatal("missing current provider snapshot should be rejected")
	}
}

func TestAlignWriterWriteBackRevised_CopiesIdentityKeepsContent(t *testing.T) {
	source := json.RawMessage(`{
		"document_id":"doc-1",
		"stage":"final",
		"title":"Baseline",
		"revision":"42",
		"provider_binding":{"provider":"feishu","document_id":"fs-1","revision":"42"},
		"blocks":[{"node_id":"a","type":"paragraph","content":"old"}]
	}`)
	revised := json.RawMessage(`{
		"document_id":"doc-1",
		"stage":"draft",
		"title":"Edited",
		"revision":"99",
		"provider_binding":{"provider":"feishu","document_id":"fs-1"},
		"blocks":[{"node_id":"a","type":"paragraph","content":"new"}]
	}`)

	aligned, err := alignWriterWriteBackRevised(source, revised)
	if err != nil {
		t.Fatalf("align: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(aligned, &got); err != nil {
		t.Fatalf("unmarshal aligned: %v", err)
	}
	if got["title"] != "Edited" {
		t.Fatalf("title = %#v, want Edited", got["title"])
	}
	if got["revision"] != "42" {
		t.Fatalf("revision = %#v, want 42", got["revision"])
	}
	if got["stage"] != "final" {
		t.Fatalf("stage = %#v, want final", got["stage"])
	}
	binding, _ := got["provider_binding"].(map[string]any)
	if binding["revision"] != "42" {
		t.Fatalf("provider_binding.revision = %#v, want 42", binding["revision"])
	}
	blocks, _ := got["blocks"].([]any)
	if len(blocks) != 1 {
		t.Fatalf("blocks len = %d, want 1", len(blocks))
	}
	block, _ := blocks[0].(map[string]any)
	if block["content"] != "new" {
		t.Fatalf("block content = %#v, want new", block["content"])
	}
}

func TestAlignWriterWriteBackRevised_ClearsMissingIdentityFields(t *testing.T) {
	source := json.RawMessage(`{
		"document_id":"doc-1",
		"stage":"final",
		"title":"Baseline",
		"provider_binding":{"provider":"feishu","document_id":"fs-1"},
		"blocks":[]
	}`)
	revised := json.RawMessage(`{
		"document_id":"doc-1",
		"stage":"draft",
		"title":"Edited",
		"revision":"99",
		"provider_binding":{"provider":"feishu","document_id":"fs-1","revision":"99"},
		"blocks":[]
	}`)

	aligned, err := alignWriterWriteBackRevised(source, revised)
	if err != nil {
		t.Fatalf("align: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(aligned, &got); err != nil {
		t.Fatalf("unmarshal aligned: %v", err)
	}
	if _, ok := got["revision"]; ok {
		t.Fatalf("revision should be cleared when baseline omits it, got %#v", got["revision"])
	}
	binding, _ := got["provider_binding"].(map[string]any)
	if _, ok := binding["revision"]; ok {
		t.Fatalf("provider_binding.revision should follow baseline, got %#v", binding["revision"])
	}
	if got["stage"] != "final" {
		t.Fatalf("stage = %#v, want final", got["stage"])
	}
}

func TestWriterArtifactRevisionSynced_ComparesOverlayWithSnapshot(t *testing.T) {
	humanID := "pha_overlay"
	clean := &selectedWriterArtifact{
		Revision: orm.PluginSlotRevision{
			ChangeSource:    "provider_sync",
			HumanArtifactID: &humanID,
			ContentSnapshot: json.RawMessage(`{"data":{"title":"same"}}`),
		},
		Value: json.RawMessage(`{ "data": { "title": "same" } }`),
	}
	if !writerArtifactRevisionSynced(clean) {
		t.Fatal("semantically unchanged overlay should remain synchronized")
	}
	dirty := &selectedWriterArtifact{
		Revision: orm.PluginSlotRevision{
			ChangeSource:    "provider_sync",
			HumanArtifactID: &humanID,
			ContentSnapshot: json.RawMessage(`{"data":{"title":"before"}}`),
		},
		Value: json.RawMessage(`{"data":{"title":"after"}}`),
	}
	if writerArtifactRevisionSynced(dirty) {
		t.Fatal("changed overlay should require write-back")
	}
}

func seedWriterRevision(
	t *testing.T,
	db *orm.DB,
	id, slotID string,
	revision int,
	selected bool,
	changeSource string,
	content json.RawMessage,
) {
	t.Helper()
	if err := db.Create(&orm.PluginSlotRevision{
		ID:              id,
		SessionID:       "session",
		SlotID:          slotID,
		Revision:        revision,
		Selected:        selected,
		ContentSnapshot: content,
		ChangeSource:    changeSource,
		Slot:            slotID,
		StepID:          "write_document",
		Attempt:         1,
		CreatedAt:       time.Now().UTC(),
	}).Error; err != nil {
		t.Fatalf("seed revision %s: %v", id, err)
	}
}
