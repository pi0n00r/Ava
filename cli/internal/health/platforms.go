package health

import (
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

type PlatformContext struct {
	OSID     string
	OSFamily string
	Key      string
	Platform map[string]interface{}
}

func docsURL(pathOrURL string) string {
	if pathOrURL == "" {
		return ""
	}
	if strings.HasPrefix(pathOrURL, "http://") || strings.HasPrefix(pathOrURL, "https://") {
		return pathOrURL
	}
	base := os.Getenv("AAVA_DOCS_BASE_URL")
	if base == "" {
		base = "https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk/blob/main/"
	}
	return strings.TrimRight(base, "/") + "/" + strings.TrimLeft(pathOrURL, "/")
}

func DetectPlatformContext() *PlatformContext {
	osID, osFamily := detectOS()

	raw := loadPlatformsYAML()
	if raw == nil {
		return &PlatformContext{OSID: osID, OSFamily: osFamily}
	}

	key := selectPlatformKey(raw, osID, osFamily)
	platform := resolvePlatform(raw, key)
	return &PlatformContext{
		OSID:     osID,
		OSFamily: osFamily,
		Key:      key,
		Platform: platform,
	}
}

func detectOS() (string, string) {
	// Sangoma / FreePBX distro detection (CentOS7-based)
	if fileExists("/etc/sangoma/pbx") || fileExists("/etc/freepbx.conf") {
		return "sangoma", "rhel"
	}

	id := "unknown"
	family := "unknown"

	raw, err := os.ReadFile("/etc/os-release")
	if err == nil {
		for _, line := range strings.Split(string(raw), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "ID=") {
				id = strings.Trim(strings.TrimPrefix(line, "ID="), "\"")
				break
			}
		}
	}

	switch id {
	case "ubuntu", "debian", "linuxmint":
		family = "debian"
	case "centos", "rhel", "rocky", "almalinux", "fedora", "sangoma":
		family = "rhel"
	}
	return id, family
}

func loadPlatformsYAML() map[string]interface{} {
	candidates := []string{
		"config/platforms.yaml",
		"/app/project/config/platforms.yaml",
		"/app/config/platforms.yaml",
		"../config/platforms.yaml",
	}

	for _, p := range candidates {
		if !fileExists(p) {
			continue
		}
		raw, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		var out map[string]interface{}
		if err := yaml.Unmarshal(raw, &out); err != nil {
			continue
		}
		return out
	}
	return nil
}

func selectPlatformKey(raw map[string]interface{}, osID, osFamily string) string {
	if raw == nil {
		return ""
	}

	// Prefer exact match if a platform section exists.
	if node, ok := raw[osID]; ok {
		if _, ok := node.(map[string]interface{}); ok {
			return osID
		}
	}

	// Search by os_ids membership.
	for key, node := range raw {
		m, ok := node.(map[string]interface{})
		if !ok {
			continue
		}
		ids := toStringSlice(m["os_ids"])
		for _, v := range ids {
			if v == osID {
				return key
			}
		}
	}

	// Family fallback.
	if node, ok := raw[osFamily]; ok {
		if _, ok := node.(map[string]interface{}); ok {
			return osFamily
		}
	}

	return ""
}

func resolvePlatform(raw map[string]interface{}, key string) map[string]interface{} {
	if raw == nil || key == "" {
		return nil
	}
	node, ok := raw[key].(map[string]interface{})
	if !ok {
		return nil
	}

	if parentKey, ok := node["inherit"].(string); ok && parentKey != "" {
		parent := resolvePlatform(raw, parentKey)
		return deepMerge(parent, node)
	}
	return deepMerge(nil, node)
}

func deepMerge(base, override map[string]interface{}) map[string]interface{} {
	out := map[string]interface{}{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range override {
		if k == "inherit" {
			continue
		}
		if mv, ok := v.(map[string]interface{}); ok {
			if bv, ok := out[k].(map[string]interface{}); ok {
				out[k] = deepMerge(bv, mv)
				continue
			}
		}
		out[k] = v
	}
	return out
}

func getString(node map[string]interface{}, keys ...string) string {
	var cur interface{} = node
	for _, k := range keys {
		m, ok := cur.(map[string]interface{})
		if !ok {
			return ""
		}
		cur = m[k]
	}
	s, _ := cur.(string)
	return strings.TrimSpace(s)
}

func toStringSlice(v interface{}) []string {
	raw, ok := v.([]interface{})
	if !ok {
		return nil
	}
	out := make([]string, 0, len(raw))
	for _, item := range raw {
		if s, ok := item.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

func fileExists(path string) bool {
	if path == "" {
		return false
	}
	if strings.Contains(path, "..") {
		path = filepath.Clean(path)
	}
	_, err := os.Stat(path)
	return err == nil
}

