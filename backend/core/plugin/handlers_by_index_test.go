package plugin

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gorilla/mux"

	"lazymind/core/common/orm"
)

func TestPatchSlotItemByIndexHonorsBaseRevision(t *testing.T) {
	db := newHandlerTestDB(t)
	if err := db.AutoMigrate(&orm.PluginHumanArtifact{}); err != nil {
		t.Fatalf("migrate human artifacts: %v", err)
	}
	now := time.Now().UTC()
	if err := db.Create(&orm.PluginSession{
		ID: "session-1", ConversationID: "conversation-1", PluginID: "writer-plugin",
		Status: SessionStatusActive, CreatedAt: now, UpdatedAt: now,
	}).Error; err != nil {
		t.Fatalf("create session: %v", err)
	}
	if err := db.Create(&orm.PluginSlotRevision{
		ID: "revision-3", SessionID: "session-1", SlotID: "draft_document",
		Revision: 3, Selected: true, ChangeSource: "ai", Slot: "draft_document",
		StepID: "write_document", Attempt: 1, CreatedAt: now,
	}).Error; err != nil {
		t.Fatalf("create selected revision: %v", err)
	}

	request := func(baseRevision int) *httptest.ResponseRecorder {
		req := httptest.NewRequest(
			http.MethodPatch,
			"/plugin-sessions/session-1/slots/draft_document/items/idx/-1",
			jsonBody(fmt.Sprintf(`{"value":{"path":"/var/lib/lazymind/uploads/draft.md","filename":"draft.md"},"content_type":"file","mode":"checkpoint","base_revision":%d}`, baseRevision)),
		)
		req = mux.SetURLVars(req, map[string]string{
			"session_id": "session-1", "slot_id": "draft_document", "list_index": "-1",
		})
		rec := httptest.NewRecorder()
		PatchSlotItemByIndex(rec, req)
		return rec
	}

	if rec := request(3); rec.Code != http.StatusOK {
		t.Fatalf("matching revision save: got %d, body=%s", rec.Code, rec.Body.String())
	}
	if rec := request(2); rec.Code != http.StatusConflict {
		t.Fatalf("stale revision save: got %d, body=%s", rec.Code, rec.Body.String())
	}
	if rec := request(3); rec.Code != http.StatusOK {
		t.Fatalf("overwrite draft again: got %d, body=%s", rec.Code, rec.Body.String())
	}

	var revisions []orm.PluginSlotRevision
	if err := db.Where("session_id = ? AND slot_id = ?", "session-1", "draft_document").
		Order("revision ASC").Find(&revisions).Error; err != nil {
		t.Fatalf("list revisions: %v", err)
	}
	// Unsynced AI draft is absorbed in place; local saves must not bump revision.
	if len(revisions) != 1 || revisions[0].Revision != 3 || !revisions[0].Selected {
		t.Fatalf("unexpected revisions: %#v", revisions)
	}
	if revisions[0].ChangeSource != "human" {
		t.Fatalf("change_source = %q, want human", revisions[0].ChangeSource)
	}
	var artifacts []orm.PluginHumanArtifact
	if err := db.Find(&artifacts).Error; err != nil {
		t.Fatalf("list human artifacts: %v", err)
	}
	if len(artifacts) != 1 || artifacts[0].ContentType != "file" {
		t.Fatalf("unexpected human artifacts: %#v", artifacts)
	}
}

func TestPatchSlotItemByIndexOverlaysAfterProviderSync(t *testing.T) {
	db := newHandlerTestDB(t)
	if err := db.AutoMigrate(&orm.PluginHumanArtifact{}); err != nil {
		t.Fatalf("migrate human artifacts: %v", err)
	}
	now := time.Now().UTC()
	if err := db.Create(&orm.PluginSession{
		ID: "session-2", ConversationID: "conversation-2", PluginID: "writer-plugin",
		Status: SessionStatusActive, CreatedAt: now, UpdatedAt: now,
	}).Error; err != nil {
		t.Fatalf("create session: %v", err)
	}
	if err := db.Create(&orm.PluginSlotRevision{
		ID: "revision-synced", SessionID: "session-2", SlotID: "draft_document",
		Revision: 1, Selected: true, ChangeSource: "provider_sync", Slot: "draft_document",
		StepID: "write_document", Attempt: 1,
		ContentSnapshot: json.RawMessage(`{"path":"/var/lib/lazymind/uploads/baseline.md","filename":"draft2.md"}`),
		CreatedAt:       now,
	}).Error; err != nil {
		t.Fatalf("create selected revision: %v", err)
	}

	patch := func(value string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(
			http.MethodPatch,
			"/plugin-sessions/session-2/slots/draft_document/items/idx/-1",
			jsonBody(fmt.Sprintf(`{"value":{"path":"%s","filename":"draft2.md"},"content_type":"file","mode":"draft","base_revision":1}`, value)),
		)
		req = mux.SetURLVars(req, map[string]string{
			"session_id": "session-2", "slot_id": "draft_document", "list_index": "-1",
		})
		rec := httptest.NewRecorder()
		PatchSlotItemByIndex(rec, req)
		return rec
	}

	if rec := patch("/var/lib/lazymind/uploads/draft2.md"); rec.Code != http.StatusOK {
		t.Fatalf("overlay draft after sync: got %d, body=%s", rec.Code, rec.Body.String())
	}
	if rec := patch("/var/lib/lazymind/uploads/draft3.md"); rec.Code != http.StatusOK {
		t.Fatalf("update overlay after sync: got %d, body=%s", rec.Code, rec.Body.String())
	}

	var revisions []orm.PluginSlotRevision
	if err := db.Where("session_id = ? AND slot_id = ?", "session-2", "draft_document").
		Order("revision ASC").Find(&revisions).Error; err != nil {
		t.Fatalf("list revisions: %v", err)
	}
	if len(revisions) != 1 {
		t.Fatalf("expected single revision overlay, got %#v", revisions)
	}
	if revisions[0].Revision != 1 || revisions[0].ChangeSource != "provider_sync" || !revisions[0].Selected {
		t.Fatalf("synced revision should stay selected at same number: %#v", revisions[0])
	}
	if revisions[0].HumanArtifactID == nil || *revisions[0].HumanArtifactID == "" {
		t.Fatalf("expected human overlay artifact on synced revision")
	}
	if string(revisions[0].ContentSnapshot) != `{"path":"/var/lib/lazymind/uploads/baseline.md","filename":"draft2.md"}` {
		t.Fatalf("provider baseline changed during local edits: %s", revisions[0].ContentSnapshot)
	}

	var artifacts []orm.PluginHumanArtifact
	if err := db.Find(&artifacts).Error; err != nil {
		t.Fatalf("list human artifacts: %v", err)
	}
	if len(artifacts) != 1 {
		t.Fatalf("expected one human overlay artifact, got %#v", artifacts)
	}
}
