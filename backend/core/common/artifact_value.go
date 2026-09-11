package common

import (
	"bytes"
	"encoding/json"
	"strings"
)

// CanonicalizeTextArtifactValue gives JSON root strings the stable object shape
// used by text Artifact consumers. Other JSON shapes and non-text values retain
// their original bytes so metadata and typed carriers are not rewritten.
func CanonicalizeTextArtifactValue(contentType string, value json.RawMessage) json.RawMessage {
	if !IsTextArtifactContentType(contentType) {
		return value
	}
	trimmed := bytes.TrimSpace(value)
	if len(trimmed) == 0 || trimmed[0] != '"' {
		return value
	}
	var text string
	if err := json.Unmarshal(trimmed, &text); err != nil {
		return value
	}
	normalized, err := json.Marshal(struct {
		Text string `json:"text"`
	}{Text: text})
	if err != nil {
		return value
	}
	return normalized
}

// IsTextArtifactContentType reports whether a short logical type or MIME value
// belongs to the text Artifact family. MIME parameters and case are irrelevant.
func IsTextArtifactContentType(contentType string) bool {
	mediaType := strings.ToLower(strings.TrimSpace(strings.SplitN(contentType, ";", 2)[0]))
	return mediaType == "text" || strings.HasPrefix(mediaType, "text/")
}
