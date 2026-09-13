#!/usr/bin/env python3
"""
Test Call Log Analysis Script

This script analyzes captured structured JSON logs from test calls
to identify issues and provide troubleshooting insights.

Usage:
    python scripts/analyze_logs.py logs/test-call-logs-20241217-143022.json
"""

import json
import sys
import argparse
from datetime import datetime
from collections import defaultdict, Counter
from typing import Dict, List, Any, Tuple
import re

class LogAnalyzer:
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.data = None
        self.issues = []
        self.insights = []
        
    def load_logs(self) -> bool:
        """Load the log file."""
        try:
            with open(self.log_file, 'r') as f:
                self.data = json.load(f)
            return True
        except Exception as e:
            print(f"❌ Failed to load log file: {e}")
            return False
    
    def analyze(self):
        """Perform comprehensive log analysis."""
        if not self.load_logs():
            return False
            
        print(f"🔍 Analyzing logs from: {self.log_file}")
        print(f"📊 Total entries: {len(self.data.get('logs', []))}")
        print(f"⏱️  Duration: {self.data.get('capture_session', {}).get('duration_seconds', 'unknown')} seconds")
        print()
        
        # Perform various analyses
        self._analyze_timeline()
        self._analyze_errors()
        self._analyze_audio_flow()
        self._analyze_ari_events()
        self._analyze_provider_interactions()
        self._analyze_performance()
        
        # Print results
        self._print_analysis_results()
        
        return True
    
    def _analyze_timeline(self):
        """Analyze the timeline of events."""
        logs = self.data.get('logs', [])
        
        # Group by timestamp and service
        timeline = defaultdict(list)
        for log in logs:
            timestamp = log.get('timestamp', log.get('capture_timestamp', ''))
            service = log.get('source_service', 'unknown')
            event = log.get('event', 'unknown')
            level = log.get('level', 'info')
            
            timeline[timestamp].append({
                'service': service,
                'event': event,
                'level': level,
                'message': log.get('message', '')
            })
        
        # Find key events
        key_events = []
        for timestamp, events in sorted(timeline.items()):
            for event in events:
                if any(keyword in event['event'].lower() for keyword in 
                      ['call', 'stasis', 'audiosocket', 'connection', 'playback', 'error']):
                    key_events.append((timestamp, event))
        
        self.insights.append({
            'type': 'timeline',
            'title': 'Key Events Timeline',
            'events': key_events[:20]  # First 20 key events
        })
    
    def _analyze_errors(self):
        """Analyze errors and warnings."""
        logs = self.data.get('logs', [])
        
        errors = []
        warnings = []
        
        for log in logs:
            level = log.get('level', 'info')
            if level == 'error':
                errors.append({
                    'timestamp': log.get('timestamp', log.get('capture_timestamp', '')),
                    'service': log.get('source_service', 'unknown'),
                    'event': log.get('event', 'unknown'),
                    'message': log.get('message', ''),
                    'error': log.get('error', '')
                })
            elif level == 'warning':
                warnings.append({
                    'timestamp': log.get('timestamp', log.get('capture_timestamp', '')),
                    'service': log.get('source_service', 'unknown'),
                    'event': log.get('event', 'unknown'),
                    'message': log.get('message', '')
                })
        
        if errors:
            self.issues.append({
                'type': 'errors',
                'title': f'Errors Found ({len(errors)})',
                'items': errors
            })
        
        if warnings:
            self.insights.append({
                'type': 'warnings',
                'title': f'Warnings Found ({len(warnings)})',
                'items': warnings[:10]  # First 10 warnings
            })
    
    def _analyze_audio_flow(self):
        """Analyze audio processing flow."""
        logs = self.data.get('logs', [])
        
        audio_events = []
        for log in logs:
            event = log.get('event', '').lower()
            if any(keyword in event for keyword in ['audio', 'chunk', 'frame', 'speech', 'vad', 'stt', 'tts']):
                audio_events.append({
                    'timestamp': log.get('timestamp', log.get('capture_timestamp', '')),
                    'service': log.get('source_service', 'unknown'),
                    'event': log.get('event', ''),
                    'level': log.get('level', 'info'),
                    'message': log.get('message', ''),
                    'bytes': log.get('bytes'),
                    'chunk_number': log.get('chunk_number'),
                    'frames_generated': log.get('frames_generated')
                })
        
        # Analyze audio flow patterns
        if audio_events:
            # Check for audio capture start
            audio_start = any('audiosocket' in event['event'].lower() for event in audio_events)
            # Check for speech detection
            speech_detected = any('speech' in event['event'].lower() for event in audio_events)
            # Check for TTS generation
            tts_generated = any('tts' in event['event'].lower() or 'playback' in event['event'].lower() for event in audio_events)
            
            self.insights.append({
                'type': 'audio_flow',
                'title': 'Audio Processing Flow',
                'audio_start': audio_start,
                'speech_detected': speech_detected,
                'tts_generated': tts_generated,
                'total_audio_events': len(audio_events),
                'events': audio_events[:15]  # First 15 audio events
            })
            
            # Check for issues
            if not audio_start:
                self.issues.append({
                    'type': 'audio_issue',
                    'title': 'AudioSocket Connection Issue',
                    'description': 'No AudioSocket connection events found'
                })
            
            if not speech_detected:
                self.issues.append({
                    'type': 'audio_issue',
                    'title': 'Speech Detection Issue',
                    'description': 'No speech detection events found'
                })
    
    def _analyze_ari_events(self):
        """Analyze ARI events."""
        logs = self.data.get('logs', [])
        
        ari_events = []
        for log in logs:
            event = log.get('event', '').lower()
            if any(keyword in event for keyword in ['stasis', 'channel', 'bridge', 'playback', 'ari']):
                ari_events.append({
                    'timestamp': log.get('timestamp', log.get('capture_timestamp', '')),
                    'service': log.get('source_service', 'unknown'),
                    'event': log.get('event', ''),
                    'level': log.get('level', 'info'),
                    'message': log.get('message', ''),
                    'channel_id': log.get('channel_id'),
                    'bridge_id': log.get('bridge_id')
                })
        
        if ari_events:
            self.insights.append({
                'type': 'ari_events',
                'title': 'ARI Events',
                'total_events': len(ari_events),
                'events': ari_events[:10]  # First 10 ARI events
            })
    
    def _analyze_provider_interactions(self):
        """Analyze AI provider interactions."""
        logs = self.data.get('logs', [])
        
        provider_events = []
        for log in logs:
            event = log.get('event', '').lower()
            if any(keyword in event for keyword in ['provider', 'local', 'deepgram', 'openai', 'llm', 'stt', 'tts']):
                provider_events.append({
                    'timestamp': log.get('timestamp', log.get('capture_timestamp', '')),
                    'service': log.get('source_service', 'unknown'),
                    'event': log.get('event', ''),
                    'level': log.get('level', 'info'),
                    'message': log.get('message', ''),
                    'provider': log.get('provider'),
                    'response_time': log.get('response_time')
                })
        
        if provider_events:
            self.insights.append({
                'type': 'provider_interactions',
                'title': 'AI Provider Interactions',
                'total_events': len(provider_events),
                'events': provider_events[:10]  # First 10 provider events
            })
    
    def _analyze_performance(self):
        """Analyze performance metrics."""
        logs = self.data.get('logs', [])
        
        # Extract timing information
        response_times = []
        for log in logs:
            if 'response_time' in log:
                try:
                    response_times.append(float(log['response_time']))
                except (ValueError, TypeError):
                    pass
        
        if response_times:
            avg_response_time = sum(response_times) / len(response_times)
            max_response_time = max(response_times)
            min_response_time = min(response_times)
            
            self.insights.append({
                'type': 'performance',
                'title': 'Performance Metrics',
                'avg_response_time': avg_response_time,
                'max_response_time': max_response_time,
                'min_response_time': min_response_time,
                'total_measurements': len(response_times)
            })
    
    def _print_analysis_results(self):
        """Print the analysis results following the call-framework.md pattern."""
        # Generate framework analysis
        framework = self._generate_call_framework()
        
        # Print the framework
        print(framework)
    
    def _generate_call_framework(self) -> str:
        """Generate call framework analysis following the established pattern."""
        logs = self.data.get('logs', [])
        session = self.data.get('capture_session', {})
        
        # Determine overall result
        has_errors = any(issue['type'] == 'errors' for issue in self.issues)
        has_audio_issues = any(issue['type'] == 'audio_issue' for issue in self.issues)
        
        if has_errors or has_audio_issues:
            result_status = "❌ **AUDIO ISSUES DETECTED**"
            result_desc = "Audio pipeline issues identified"
        else:
            result_status = "✅ **SUCCESS**"
            result_desc = "Audio pipeline working correctly"
        
        # Extract key phases
        phases = self._extract_call_phases(logs)
        
        # Generate framework
        framework = f"""# Call Framework Analysis - Test Call ({session.get('start_time', 'Unknown')})

## Executive Summary
**Test Call Result**: {result_status} - {result_desc}

**Root Cause**: {self._generate_root_cause_analysis()}

## Call Timeline Analysis

{self._generate_phase_analysis(phases)}

## Root Cause Analysis

{self._generate_detailed_root_cause()}

## Critical Issues Identified

{self._generate_critical_issues()}

## Recommended Fixes

{self._generate_recommended_fixes()}

## Confidence Score: {self._calculate_confidence_score()}/10

{self._generate_confidence_explanation()}

## Next Steps

{self._generate_next_steps()}

## Call Framework Summary

{self._generate_framework_summary(phases)}

**Overall Result**: {result_status} - {result_desc}
"""
        return framework
    
    def _extract_call_phases(self, logs):
        """Extract call phases from logs."""
        phases = {
            'call_initiation': {'status': 'unknown', 'events': []},
            'bridge_creation': {'status': 'unknown', 'events': []},
            'audiosocket_origination': {'status': 'unknown', 'events': []},
            'audiosocket_connection': {'status': 'unknown', 'events': []},
            'audio_processing': {'status': 'unknown', 'events': []},
            'greeting_audio': {'status': 'unknown', 'events': []},
            'bridge_connection': {'status': 'unknown', 'events': []},
            'call_cleanup': {'status': 'unknown', 'events': []}
        }
        
        for log in logs:
            event = log.get('event', '').lower()
            timestamp = log.get('timestamp', log.get('capture_timestamp', ''))
            service = log.get('source_service', 'unknown')
            level = log.get('level', 'info')
            message = log.get('message', '')
            
            # Categorize events by phase
            if any(keyword in event for keyword in ['stasis', 'call', 'channel', 'answer']):
                phases['call_initiation']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['bridge', 'created']):
                phases['bridge_creation']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['audiosocket', 'originate', 'local']):
                phases['audiosocket_origination']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['connection', 'accepted', 'peer']):
                phases['audiosocket_connection']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['audio', 'chunk', 'frame', 'speech', 'vad']):
                phases['audio_processing']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['greeting', 'tts', 'playback', 'speak']):
                phases['greeting_audio']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['bridge', 'added', 'channel']):
                phases['bridge_connection']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
            elif any(keyword in event for keyword in ['cleanup', 'destroyed', 'end']):
                phases['call_cleanup']['events'].append({
                    'timestamp': timestamp,
                    'service': service,
                    'event': event,
                    'level': level,
                    'message': message
                })
        
        # Determine phase status
        for phase_name, phase_data in phases.items():
            events = phase_data['events']
            if not events:
                phase_data['status'] = 'unknown'
            elif any(event['level'] == 'error' for event in events):
                phase_data['status'] = 'failure'
            elif any(event['level'] in ['info', 'debug'] for event in events):
                phase_data['status'] = 'success'
            else:
                phase_data['status'] = 'unknown'
        
        return phases
    
    def _generate_root_cause_analysis(self):
        """Generate root cause analysis."""
        if not self.issues:
            return "No critical issues identified"
        
        root_causes = []
        for issue in self.issues:
            if issue['type'] == 'audio_issue':
                if 'AudioSocket' in issue['title']:
                    root_causes.append("AudioSocket connection issues")
                elif 'Speech Detection' in issue['title']:
                    root_causes.append("Speech detection pipeline failure")
            elif issue['type'] == 'errors':
                root_causes.append("System errors detected")
        
        return "; ".join(root_causes) if root_causes else "Unknown issues"
    
    def _generate_phase_analysis(self, phases):
        """Generate phase analysis section."""
        phase_analysis = ""
        
        phase_descriptions = {
            'call_initiation': 'Call Initiation',
            'bridge_creation': 'Bridge Creation', 
            'audiosocket_origination': 'AudioSocket Channel Origination',
            'audiosocket_connection': 'AudioSocket Connection',
            'audio_processing': 'Audio Processing',
            'greeting_audio': 'Greeting Audio',
            'bridge_connection': 'Bridge Connection',
            'call_cleanup': 'Call Cleanup'
        }
        
        for phase_name, phase_data in phases.items():
            if not phase_data['events']:
                continue
                
            status_icon = "✅" if phase_data['status'] == 'success' else "❌" if phase_data['status'] == 'failure' else "⚠️"
            status_text = "SUCCESS" if phase_data['status'] == 'success' else "FAILURE" if phase_data['status'] == 'failure' else "UNKNOWN"
            
            phase_analysis += f"### {phase_descriptions[phase_name]}\n"
            phase_analysis += f"**Status**: {status_icon} **{status_text}**\n\n"
            
            # Show key events
            key_events = phase_data['events'][:5]  # First 5 events
            for event in key_events:
                phase_analysis += f"```\n[{event['timestamp']}] [{event['service']}] {event['event']} - {event['message']}\n```\n"
            
            phase_analysis += "\n"
        
        return phase_analysis
    
    def _generate_detailed_root_cause(self):
        """Generate detailed root cause analysis."""
        if not self.issues:
            return "No detailed analysis needed - no issues found."
        
        analysis = ""
        for i, issue in enumerate(self.issues, 1):
            analysis += f"### {i}. **{issue['title']}**\n"
            analysis += f"**Problem**: {issue.get('description', 'Issue identified')}\n"
            analysis += f"**Impact**: {self._get_issue_impact(issue)}\n"
            analysis += f"**Evidence**: {self._get_issue_evidence(issue)}\n\n"
        
        return analysis
    
    def _generate_critical_issues(self):
        """Generate critical issues section."""
        if not self.issues:
            return "No critical issues identified."
        
        issues_text = ""
        for i, issue in enumerate(self.issues, 1):
            issues_text += f"### Issue #{i}: {issue['title']}\n"
            issues_text += f"**Current**: {self._get_current_state(issue)}\n"
            issues_text += f"**Required**: {self._get_required_state(issue)}\n\n"
        
        return issues_text
    
    def _generate_recommended_fixes(self):
        """Generate recommended fixes section."""
        if not self.issues:
            return "No fixes needed - system working correctly."
        
        fixes_text = ""
        for i, issue in enumerate(self.issues, 1):
            fixes_text += f"### Fix #{i}: {issue['title']}\n"
            fixes_text += f"```python\n{self._get_fix_code(issue)}\n```\n\n"
        
        return fixes_text
    
    def _calculate_confidence_score(self):
        """Calculate confidence score based on analysis."""
        if not self.issues:
            return 10
        
        # Reduce confidence based on number and severity of issues
        score = 10
        for issue in self.issues:
            if issue['type'] == 'errors':
                score -= 3
            elif issue['type'] == 'audio_issue':
                score -= 2
        
        return max(1, score)
    
    def _generate_confidence_explanation(self):
        """Generate confidence explanation."""
        score = self._calculate_confidence_score()
        if score >= 8:
            return "High confidence in analysis - clear patterns identified."
        elif score >= 5:
            return "Medium confidence - some uncertainty in root cause."
        else:
            return "Low confidence - multiple potential issues identified."
    
    def _generate_next_steps(self):
        """Generate next steps."""
        if not self.issues:
            return "1. **Verify system health** - Run health checks\n2. **Monitor performance** - Continue monitoring\n3. **Document success** - Update documentation"
        
        steps = []
        for i, issue in enumerate(self.issues, 1):
            steps.append(f"{i}. **Fix {issue['title']}** - {self._get_fix_description(issue)}")
        
        return "\n".join(steps)
    
    def _generate_framework_summary(self, phases):
        """Generate framework summary table."""
        summary = "| Phase | Status | Issue |\n"
        summary += "|-------|--------|-------|\n"
        
        phase_names = {
            'call_initiation': 'Call Initiation',
            'bridge_creation': 'Bridge Creation',
            'audiosocket_origination': 'AudioSocket Origination', 
            'audiosocket_connection': 'AudioSocket Connection',
            'audio_processing': 'Audio Processing',
            'greeting_audio': 'Greeting Audio',
            'bridge_connection': 'Bridge Connection',
            'call_cleanup': 'Call Cleanup'
        }
        
        for phase_name, phase_data in phases.items():
            if phase_data['events']:
                status_icon = "✅" if phase_data['status'] == 'success' else "❌" if phase_data['status'] == 'failure' else "⚠️"
                issue_text = "None" if phase_data['status'] == 'success' else "See analysis above"
                summary += f"| {phase_names[phase_name]} | {status_icon} {phase_data['status'].title()} | {issue_text} |\n"
        
        return summary
    
    # Helper methods for generating specific content
    def _get_issue_impact(self, issue):
        """Get issue impact description."""
        if issue['type'] == 'audio_issue':
            if 'AudioSocket' in issue['title']:
                return "No audio processing capability"
            elif 'Speech Detection' in issue['title']:
                return "No speech recognition"
        return "System functionality affected"
    
    def _get_issue_evidence(self, issue):
        """Get issue evidence."""
        if 'items' in issue and issue['items']:
            return f"Found {len(issue['items'])} related log entries"
        return "Log analysis indicates issue"
    
    def _get_current_state(self, issue):
        """Get current state description."""
        if issue['type'] == 'audio_issue':
            if 'AudioSocket' in issue['title']:
                return "AudioSocket handler not executing properly"
            elif 'Speech Detection' in issue['title']:
                return "Speech detection pipeline not working"
        return "Issue present in system"
    
    def _get_required_state(self, issue):
        """Get required state description."""
        if issue['type'] == 'audio_issue':
            if 'AudioSocket' in issue['title']:
                return "AudioSocket handler executing correctly"
            elif 'Speech Detection' in issue['title']:
                return "Speech detection pipeline working"
        return "Issue resolved"
    
    def _get_fix_code(self, issue):
        """Get fix code example."""
        if issue['type'] == 'audio_issue':
            if 'AudioSocket' in issue['title']:
                return "# Fix AudioSocket handler awaiting\nawait self.on_accept(conn_id)"
            elif 'Speech Detection' in issue['title']:
                return "# Fix speech detection\n# Check VAD settings and audio format conversion"
        return "# Fix implementation needed"
    
    def _get_fix_description(self, issue):
        """Get fix description."""
        if issue['type'] == 'audio_issue':
            if 'AudioSocket' in issue['title']:
                return "Ensure AudioSocket handler is properly awaited"
            elif 'Speech Detection' in issue['title']:
                return "Fix speech detection pipeline"
        return "Resolve identified issue"

def main():
    parser = argparse.ArgumentParser(description='Analyze test call logs')
    parser.add_argument('log_file', help='Path to the JSON log file to analyze')
    
    args = parser.parse_args()
    
    analyzer = LogAnalyzer(args.log_file)
    if not analyzer.analyze():
        sys.exit(1)

if __name__ == "__main__":
    main()
