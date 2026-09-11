package store

import (
	"context"
	"errors"

	"gopkg.in/yaml.v3"
	"lazymind/core/common/orm"
	"lazymind/core/workflow/artifactgraph"
	"lazymind/core/workflow/document"
)

// DescribeArtifact is an HTTP read projection; internal mutation reads do not
// depend on Algorithm availability. The caller supplies an authorized Artifact.
func (r *Repository) DescribeArtifact(ctx context.Context, owner string, artifact *Artifact, allowSave bool) {
	artifact.Document = nil
	artifact.DocumentError = nil
	var session orm.WorkflowSession
	if err := r.db.WithContext(ctx).Where("id = ?", artifact.SessionID).First(&session).Error; err != nil {
		artifact.DocumentError = document.Unavailable()
		return
	}
	if owner == "" || session.CreateUserID != owner ||
		(ConversationScope(ctx) != "" && ConversationScope(ctx) != session.ConversationID) {
		artifact.DocumentError = document.Unavailable()
		return
	}
	writable := allowSave && artifact.Selected && artifact.Validity == "effective" && !session.Dismissed
	switch session.Status {
	case "active", "waiting", "completed", "failed":
	default:
		writable = false
	}
	artifact.Document, artifact.DocumentError = document.Project(ctx, artifact.Value, artifact.ContentType, writable, func() (bool, error) {
		if session.WorkflowRevisionID == "" {
			return false, nil
		}
		ref := session.WorkflowRef
		if ref == "" {
			ref = session.WorkflowID
		}
		pkg, err := r.GetWorkflowPackage(ctx, owner, ref, session.WorkflowRevisionID)
		if err != nil {
			return false, err
		}
		var manifest struct {
			UI struct {
				Slots map[string]struct {
					WidgetType string `yaml:"widgetType"`
				} `yaml:"slots"`
			} `yaml:"ui"`
		}
		if err := yaml.Unmarshal(pkg.Files["workflow.yaml"], &manifest); err != nil {
			return false, err
		}
		return manifest.UI.Slots[artifact.SlotID].WidgetType == "text-markdown", nil
	})
	if artifact.Document != nil && writable {
		err := artifactgraph.CheckConsumers(ctx, r.db, session.ID, artifact.ID)
		if errors.Is(err, artifactgraph.ErrArtifactInUse) {
			artifact.Document.Editable = false
			artifact.Document.Capabilities = []string{}
		} else if err != nil {
			artifact.Document = nil
			artifact.DocumentError = document.Unavailable()
		}
	}

}
