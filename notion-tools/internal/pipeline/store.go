package pipeline

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// DataDir returns .cache/<handler>/ under rootDir, creating it if needed.
func DataDir(rootDir, handler string) (string, error) {
	dir := filepath.Join(rootDir, ".cache", handler)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return "", err
	}
	return dir, nil
}

// LoadJSON reads path into v. Returns (false, nil) if the file does not exist.
func LoadJSON(path string, v any) (bool, error) {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if err := json.Unmarshal(data, v); err != nil {
		return false, err
	}
	return true, nil
}

// SaveJSON atomically writes v to path as indented JSON.
func SaveJSON(path string, v any) error {
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
