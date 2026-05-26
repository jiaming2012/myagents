package pipeline

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

const dataSubdir = ".cache/inbox"

// DataDir returns the pipeline data directory, creating it if needed.
func DataDir(root string) (string, error) {
	dir := filepath.Join(root, dataSubdir)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", err
	}
	return dir, nil
}

func emailsPath(root string) string {
	return filepath.Join(root, dataSubdir, "emails.json")
}

func insightsPath(root string) string {
	return filepath.Join(root, dataSubdir, "insights.json")
}

// ChunkPromptPath returns the path for the temporary chunk prompt file.
func ChunkPromptPath(root string) string {
	return filepath.Join(root, dataSubdir, "chunk_prompt.txt")
}

// ChunkResponsePath returns the path for the Claude response file.
func ChunkResponsePath(root string) string {
	return filepath.Join(root, dataSubdir, "chunk_response.txt")
}

// LoadDownloadManifest reads emails.json. Returns nil manifest (no error) if file doesn't exist.
func LoadDownloadManifest(root string) (*DownloadManifest, error) {
	data, err := os.ReadFile(emailsPath(root))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var m DownloadManifest
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// SaveDownloadManifest atomically writes emails.json.
func SaveDownloadManifest(root string, m *DownloadManifest) error {
	m.UpdatedAt = time.Now()
	return atomicWriteJSON(emailsPath(root), m)
}

// LoadInsightsManifest reads insights.json. Returns nil manifest (no error) if file doesn't exist.
func LoadInsightsManifest(root string) (*InsightsManifest, error) {
	data, err := os.ReadFile(insightsPath(root))
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var m InsightsManifest
	if err := json.Unmarshal(data, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// SaveInsightsManifest atomically writes insights.json.
func SaveInsightsManifest(root string, m *InsightsManifest) error {
	m.UpdatedAt = time.Now()
	return atomicWriteJSON(insightsPath(root), m)
}

func atomicWriteJSON(path string, v any) error {
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	data, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
