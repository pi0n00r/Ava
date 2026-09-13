package health

import (
	"bufio"
	"os"
	"strings"
)

// LoadEnvFile loads environment variables from .env file
// Returns a map of key-value pairs
func LoadEnvFile(path string) (map[string]string, error) {
	envMap := make(map[string]string)
	
	file, err := os.Open(path)
	if err != nil {
		return envMap, err
	}
	defer file.Close()
	
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		
		// Skip empty lines and comments
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		
		// Parse KEY=VALUE
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			key := strings.TrimSpace(parts[0])
			value := strings.TrimSpace(parts[1])
			envMap[key] = value
		}
	}
	
	return envMap, scanner.Err()
}

// GetEnv gets environment variable with fallback to .env file
func GetEnv(key string, envMap map[string]string) string {
	// First check OS environment
	if val := os.Getenv(key); val != "" {
		return val
	}
	
	// Fallback to .env map
	return envMap[key]
}
