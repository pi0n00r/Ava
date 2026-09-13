package health

import (
	"context"
	"fmt"
	"os"
	"time"
)

type CheckStatus string

const (
	StatusPass CheckStatus = "pass"
	StatusWarn CheckStatus = "warn"
	StatusFail CheckStatus = "fail"
	StatusInfo CheckStatus = "info"
)

type Check struct {
	Name        string      `json:"name"`
	Status      CheckStatus `json:"status"`
	Message     string      `json:"message"`
	Details     string      `json:"details,omitempty"`
	Remediation string      `json:"remediation,omitempty"`
}

type HealthResult struct {
	Timestamp     time.Time `json:"timestamp"`
	Checks        []Check   `json:"checks"`
	PassCount     int       `json:"pass_count"`
	WarnCount     int       `json:"warn_count"`
	CriticalCount int       `json:"critical_count"`
	InfoCount     int       `json:"info_count"`
	TotalCount    int       `json:"total_count"`
}

type Checker struct {
	verbose bool
	ctx     context.Context
	envMap  map[string]string
	platform *PlatformContext
}

func NewChecker(verbose bool) *Checker {
	// Try to load .env file
	envMap, err := LoadEnvFile(".env")
	if err != nil {
		// Try config/.env
		envMap, _ = LoadEnvFile("config/.env")
	}
	
	return &Checker{
		verbose: verbose,
		ctx:     context.Background(),
		envMap:  envMap,
		platform: DetectPlatformContext(),
	}
}

func (c *Checker) RunAll() (*HealthResult, error) {
	result := &HealthResult{
		Timestamp: time.Now(),
		Checks:    make([]Check, 0),
	}
	
	// Run all checks in sequence
	checks := []func() Check{
		c.checkDocker,
		c.checkCompose,
		c.checkContainers,
		c.checkAsteriskARI,
		c.checkAudioSocket,
		c.checkConfiguration,
		c.checkProviderKeys,
		c.checkAudioPipeline,
		c.checkNetwork,
		c.checkMediaDirectory,
		c.checkLogs,
		c.checkRecentCalls,
	}
	
	for i, checkFn := range checks {
		if c.verbose {
			fmt.Fprintf(os.Stderr, "[%d/%d] Running check...\n", i+1, len(checks))
		}
		check := checkFn()
		result.Checks = append(result.Checks, check)
		
		// Update counters
		switch check.Status {
		case StatusPass:
			result.PassCount++
		case StatusWarn:
			result.WarnCount++
		case StatusFail:
			result.CriticalCount++
		case StatusInfo:
			result.InfoCount++
		}
	}
	
	result.TotalCount = len(result.Checks)
	
	return result, nil
}

// AutoFix attempts to fix issues found during health checks
// Focus: YAML config validation (codec/sample rate alignment, provider settings)
func (c *Checker) AutoFix(result *HealthResult) (int, error) {
	fixed := 0
	
	// For now, focus on config validation issues
	// Look for configuration check failures/warnings
	for _, check := range result.Checks {
		if check.Name == "Configuration" && (check.Status == StatusFail || check.Status == StatusWarn) {
			// Delegate to config validator for auto-fix
			fmt.Println("Attempting to fix configuration issues...")
			
			// Note: This is a simplified implementation
			// Full implementation would integrate with cli/internal/config package
			fmt.Println("⚠️  Configuration issues detected")
			fmt.Println("   Run: agent config validate --fix")
			fmt.Println("   to interactively fix YAML configuration")
			
			return 0, nil
		}
	}
	
	// No fixable issues found
	return fixed, nil
}
