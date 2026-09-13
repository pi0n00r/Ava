package troubleshoot

import (
	"fmt"
	"strings"
)

// AnalyzeFormatAlignment checks config vs runtime format/sampling alignment.
//
// IMPORTANT: RCA is log-driven. We do not shell out to read config files here.
// Config-side values should come from an explicit log header (RCA_CALL_START).
func AnalyzeFormatAlignment(metrics *CallMetrics, header *RCAHeader) *FormatAlignment {
	alignment := &FormatAlignment{
		Issues: []string{},
	}

	// Prefer config-side values from the log-emitted RCA header.
	if header != nil {
		alignment.ConfigAudioTransport = strings.ToLower(strings.TrimSpace(header.AudioTransport))
		alignment.ConfigAudioSocketFormat = strings.TrimSpace(header.AudioSocketFormat)
		alignment.ConfigSampleRate = header.StreamingSampleRate
		if header.ProviderInputEncoding != "" {
			alignment.ConfigProviderInputFormat = header.ProviderInputEncoding
		} else if header.ProviderProviderInputEncoding != "" {
			alignment.ConfigProviderInputFormat = header.ProviderProviderInputEncoding
		}
		if header.ProviderOutputEncoding != "" {
			alignment.ConfigProviderOutputFormat = header.ProviderOutputEncoding
		}
	}

	// Get runtime values from logs
	alignment.RuntimeAudioSocketFormat = metrics.AudioSocketFormat
	alignment.RuntimeProviderInputFormat = metrics.ProviderInputFormat
	alignment.RuntimeSampleRate = metrics.SampleRate

	// Analyze frame sizes
	analyzeFrameSizes(alignment, metrics)

	// Detect misalignments
	detectMisalignments(alignment)

	return alignment
}

func analyzeFrameSizes(alignment *FormatAlignment, metrics *CallMetrics) {
	// Calculate expected frame size based on format
	if alignment.RuntimeAudioSocketFormat == "slin" || alignment.RuntimeAudioSocketFormat == "slin16" {
		// PCM16 @ 8kHz, 20ms frame = 320 bytes
		alignment.ExpectedFrameSize = 320
	} else if alignment.RuntimeAudioSocketFormat == "ulaw" || alignment.RuntimeAudioSocketFormat == "mulaw" {
		// μ-law @ 8kHz, 20ms frame = 160 bytes
		alignment.ExpectedFrameSize = 160
	}

	// Observe actual frame sizes from provider bytes
	if len(metrics.ProviderSegments) > 0 {
		// Take first segment as sample
		alignment.ObservedFrameSize = metrics.ProviderSegments[0].ProviderBytes / 10 // Rough estimate per frame
	}
}

func detectMisalignments(alignment *FormatAlignment) {
	transport := strings.ToLower(strings.TrimSpace(alignment.ConfigAudioTransport))

	// Check AudioSocket format mismatch
	if transport == "audiosocket" && alignment.ConfigAudioSocketFormat != "" && alignment.RuntimeAudioSocketFormat != "" {
		if alignment.ConfigAudioSocketFormat != alignment.RuntimeAudioSocketFormat {
			alignment.AudioSocketMismatch = true
			alignment.Issues = append(alignment.Issues, fmt.Sprintf(
				"AudioSocket format mismatch: config=%s, runtime=%s",
				alignment.ConfigAudioSocketFormat, alignment.RuntimeAudioSocketFormat))
		}
	}

	// Check provider format mismatch
	if alignment.ConfigProviderInputFormat != "" && alignment.RuntimeProviderInputFormat != "" {
		configNorm := normalizeFormat(alignment.ConfigProviderInputFormat)
		runtimeNorm := normalizeFormat(alignment.RuntimeProviderInputFormat)
		if configNorm != runtimeNorm {
			alignment.ProviderFormatMismatch = true
			alignment.Issues = append(alignment.Issues, fmt.Sprintf(
				"Provider input format mismatch: config=%s, runtime=%s",
				alignment.ConfigProviderInputFormat, alignment.RuntimeProviderInputFormat))
		}
	}

	// Check AudioSocket format is correct (golden baseline)
	if transport == "audiosocket" && alignment.RuntimeAudioSocketFormat != "" && alignment.RuntimeAudioSocketFormat != "slin" {
		alignment.AudioSocketMismatch = true
		alignment.Issues = append(alignment.Issues, fmt.Sprintf(
			"AudioSocket format should be 'slin' (golden baseline), got '%s'",
			alignment.RuntimeAudioSocketFormat))
	}

	// Check frame size alignment
	if alignment.ExpectedFrameSize > 0 && alignment.ObservedFrameSize > 0 {
		// Allow 10% tolerance
		diff := alignment.ExpectedFrameSize - alignment.ObservedFrameSize
		if diff < 0 {
			diff = -diff
		}
		tolerance := alignment.ExpectedFrameSize / 10
		if diff > tolerance {
			alignment.FrameSizeMismatch = true
			alignment.Issues = append(alignment.Issues, fmt.Sprintf(
				"Frame size mismatch: expected ~%d bytes, observed ~%d bytes",
				alignment.ExpectedFrameSize, alignment.ObservedFrameSize))
		}
	}
}

func normalizeFormat(format string) string {
	format = strings.ToLower(format)
	// Normalize various encodings to standard names
	switch format {
	case "mulaw", "ulaw", "pcmu":
		return "mulaw"
	case "alaw", "pcma":
		return "alaw"
	case "linear16", "pcm16", "slin", "slin16":
		return "pcm16"
	case "linear24", "pcm24":
		return "pcm24"
	default:
		return format
	}
}

// FormatMetricsForLLM formats format alignment info for LLM
func (fa *FormatAlignment) FormatForLLM() string {
	if fa == nil || len(fa.Issues) == 0 {
		return ""
	}

	var out strings.Builder
	out.WriteString("\n=== FORMAT/SAMPLING ALIGNMENT ===\n\n")

	out.WriteString("Configuration vs Runtime:\n")
	if strings.ToLower(strings.TrimSpace(fa.ConfigAudioTransport)) != "" {
		out.WriteString(fmt.Sprintf("  Transport: %s\n", fa.ConfigAudioTransport))
	}
	if strings.ToLower(strings.TrimSpace(fa.ConfigAudioTransport)) == "audiosocket" && fa.ConfigAudioSocketFormat != "" {
		out.WriteString(fmt.Sprintf("  AudioSocket: config=%s, runtime=%s",
			fa.ConfigAudioSocketFormat, fa.RuntimeAudioSocketFormat))
		if fa.AudioSocketMismatch {
			out.WriteString(" ❌ MISMATCH\n")
		} else {
			out.WriteString(" ✅\n")
		}
	}

	if fa.ConfigProviderInputFormat != "" {
		out.WriteString(fmt.Sprintf("  Provider input: config=%s, runtime=%s",
			fa.ConfigProviderInputFormat, fa.RuntimeProviderInputFormat))
		if fa.ProviderFormatMismatch {
			out.WriteString(" ❌ MISMATCH\n")
		} else {
			out.WriteString(" ✅\n")
		}
	}

	if len(fa.Issues) > 0 {
		out.WriteString("\n⚠️  ALIGNMENT ISSUES:\n")
		for i, issue := range fa.Issues {
			out.WriteString(fmt.Sprintf("%d. %s\n", i+1, issue))
		}
	}

	return out.String()
}
