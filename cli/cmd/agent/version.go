package main

import (
	"fmt"

	"github.com/spf13/cobra"
)

var versionCmd = &cobra.Command{
	Use:   "version",
	Short: "Show version information",
	Long:  "Display the version of the agent CLI tool",
	Run: func(cmd *cobra.Command, args []string) {
		fmt.Printf("Asterisk AI Voice Agent CLI\n")
		fmt.Printf("Version:    %s\n", version)
		fmt.Printf("Built:      %s\n", buildTime)
		fmt.Printf("Repository: https://github.com/hkjarral/AVA-AI-Voice-Agent-for-Asterisk\n")
	},
}

func init() {
	rootCmd.AddCommand(versionCmd)
}
