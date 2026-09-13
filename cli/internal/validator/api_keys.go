package validator

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// ValidateOpenAIKey validates an OpenAI API key
func ValidateOpenAIKey(apiKey string) error {
	client := &http.Client{Timeout: 10 * time.Second}
	
	req, err := http.NewRequest("GET", "https://api.openai.com/v1/models", nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	
	req.Header.Set("Authorization", "Bearer "+apiKey)
	
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("network error: %w (check your internet connection)", err)
	}
	defer resp.Body.Close()
	
	if resp.StatusCode == 401 {
		return fmt.Errorf("invalid API key (authentication failed)")
	}
	
	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("API returned status %d: %s", resp.StatusCode, string(body))
	}
	
	// Parse response to verify it contains models
	var result struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}
	
	if len(result.Data) == 0 {
		return fmt.Errorf("API key valid but no models available")
	}
	
	// Check for GPT models
	hasGPT := false
	for _, model := range result.Data {
		if len(model.ID) >= 3 && model.ID[:3] == "gpt" {
			hasGPT = true
			break
		}
	}
	
	if !hasGPT {
		return fmt.Errorf("API key valid but no GPT models found")
	}
	
	return nil
}

// ValidateDeepgramKey validates a Deepgram API key
func ValidateDeepgramKey(apiKey string) error {
	client := &http.Client{Timeout: 10 * time.Second}
	
	req, err := http.NewRequest("GET", "https://api.deepgram.com/v1/projects", nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	
	req.Header.Set("Authorization", "Token "+apiKey)
	
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("network error: %w (check your internet connection)", err)
	}
	defer resp.Body.Close()
	
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return fmt.Errorf("invalid API key (authentication failed)")
	}
	
	if resp.StatusCode != 200 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("API returned status %d: %s", resp.StatusCode, string(body))
	}
	
	// Parse response to verify it contains projects
	var result struct {
		Projects []struct {
			ProjectID string `json:"project_id"`
		} `json:"projects"`
	}
	
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return fmt.Errorf("failed to parse response: %w", err)
	}
	
	if len(result.Projects) == 0 {
		return fmt.Errorf("API key valid but no projects found")
	}
	
	return nil
}

// ValidateGoogleKey validates a Google API key (format check only)
func ValidateGoogleKey(apiKey string) error {
	// Google Cloud API keys have specific format patterns
	// Basic validation: should start with "AIza" and be 39 characters
	if len(apiKey) < 30 {
		return fmt.Errorf("API key appears too short (expected ~39 characters)")
	}
	
	// Note: Full validation would require making an API call
	// For now, we just do format validation
	// Real validation happens when service tries to use it
	
	return nil
}

// ValidateAPIKey validates an API key for the given provider
func ValidateAPIKey(provider, apiKey string) error {
	if apiKey == "" {
		return fmt.Errorf("API key cannot be empty")
	}
	
	switch provider {
	case "openai_realtime":
		return ValidateOpenAIKey(apiKey)
	case "deepgram":
		return ValidateDeepgramKey(apiKey)
	case "google_live":
		return ValidateGoogleKey(apiKey)
	case "local_hybrid":
		// Local hybrid doesn't need external API key
		return nil
	default:
		return fmt.Errorf("unknown provider: %s", provider)
	}
}
