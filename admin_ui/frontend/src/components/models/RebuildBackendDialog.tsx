import { useState, useEffect, useRef } from 'react';
import { AlertTriangle, CheckCircle2, XCircle, Loader2, Terminal, Clock, RotateCcw } from 'lucide-react';
import axios from 'axios';

interface RebuildProgress {
    phase: string;
    percent: number;
    estimated_seconds: number;
    elapsed_seconds: number;
    message: string;
}

interface RebuildJob {
    id: string;
    backend: string;
    running: boolean;
    completed: boolean;
    error: string | null;
    rolled_back: boolean;
    output: string[];
    progress: RebuildProgress;
}

interface RebuildBackendDialogProps {
    isOpen: boolean;
    backend: string;
    backendDisplayName: string;
    estimatedSeconds: number;
    onClose: () => void;
    onComplete: (success: boolean) => void;
}

const PHASE_LABELS: Record<string, string> = {
    pending: 'Preparing...',
    backup: 'Creating backup...',
    updating: 'Updating configuration...',
    building: 'Building container (this may take several minutes)...',
    restarting: 'Restarting service...',
    verifying: 'Verifying backend availability...',
    done: 'Complete!',
    error: 'Failed',
};

export const RebuildBackendDialog = ({
    isOpen,
    backend,
    backendDisplayName,
    estimatedSeconds,
    onClose,
    onComplete,
}: RebuildBackendDialogProps) => {
    const [stage, setStage] = useState<'confirm' | 'progress' | 'result'>('confirm');
    const [job, setJob] = useState<RebuildJob | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [alreadyEnabledMessage, setAlreadyEnabledMessage] = useState<string | null>(null);
    const outputRef = useRef<HTMLDivElement>(null);
    const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const formatTime = (seconds: number): string => {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    };

    const startRebuild = async () => {
        setStage('progress');
        setError(null);
        setAlreadyEnabledMessage(null);
        
        try {
            const res = await axios.post('/api/wizard/local/backends/enable', { backend });
            if (res.data.already_enabled) {
                setAlreadyEnabledMessage(
                    res.data.message || `${backendDisplayName} backend is already enabled and available in Local AI Server model settings.`
                );
                setStage('result');
                onComplete(true);
            } else if (res.data.job_id) {
                startPolling(res.data.job_id);
            } else if (res.data.error) {
                setError(res.data.error);
                setStage('result');
            }
        } catch (err: any) {
            setError(err.response?.data?.detail || err.message || 'Failed to start rebuild');
            setStage('result');
        }
    };

    const startPolling = (id: string) => {
        if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
        }
        
        const poll = async () => {
            try {
                const res = await axios.get(`/api/wizard/local/backends/rebuild-status?job_id=${id}`);
                if (res.data.job) {
                    setJob(res.data.job);
                    
                    // Auto-scroll output
                    if (outputRef.current) {
                        outputRef.current.scrollTop = outputRef.current.scrollHeight;
                    }
                    
                    // Check if done
                    if (!res.data.job.running) {
                        if (pollIntervalRef.current) {
                            clearInterval(pollIntervalRef.current);
                            pollIntervalRef.current = null;
                        }
                        setStage('result');
                        onComplete(res.data.job.completed);
                    }
                }
            } catch (err) {
                console.error('Failed to poll rebuild status:', err);
            }
        };
        
        // Poll immediately and then every 2 seconds
        poll();
        pollIntervalRef.current = setInterval(poll, 2000);
    };

    useEffect(() => {
        if (!isOpen) {
            // Reset state when dialog closes
            setStage('confirm');
            setJob(null);
            setError(null);
            setAlreadyEnabledMessage(null);
            if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
                pollIntervalRef.current = null;
            }
        }
    }, [isOpen]);

    useEffect(() => {
        return () => {
            if (pollIntervalRef.current) {
                clearInterval(pollIntervalRef.current);
            }
        };
    }, []);

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
            <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden">
                {/* Header */}
                <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                        {stage === 'confirm' && `Enable ${backendDisplayName} Backend`}
                        {stage === 'progress' && `Installing ${backendDisplayName}...`}
                        {stage === 'result' && (
                            alreadyEnabledMessage
                                ? 'Backend Already Enabled'
                                : (job?.completed ? 'Installation Complete' : 'Installation Failed')
                        )}
                    </h2>
                </div>

                {/* Content */}
                <div className="p-6">
                    {stage === 'confirm' && (
                        <div className="space-y-4">
                            <div className="flex items-start gap-3 p-4 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-200 dark:border-amber-800">
                                <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
                                <div className="text-sm text-amber-800 dark:text-amber-200">
                                    <p className="font-medium mb-2">This operation will:</p>
                                    <ul className="list-disc list-inside space-y-1 ml-2">
                                        <li>Update Docker build configuration</li>
                                        <li>Rebuild the local_ai_server container</li>
                                        <li>Restart the local AI service</li>
                                    </ul>
                                </div>
                            </div>

                            <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                <Clock className="w-4 h-4" />
                                <span>Estimated time: <strong>{formatTime(estimatedSeconds)}</strong></span>
                            </div>

                            <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                <AlertTriangle className="w-4 h-4" />
                                <span>Local AI will be <strong>unavailable</strong> during the rebuild</span>
                            </div>

                            <div className="bg-gray-50 dark:bg-gray-900 rounded-lg p-3 text-sm text-gray-700 dark:text-gray-300">
                                <p className="font-medium mb-1">What happens if it fails?</p>
                                <p>The system will automatically roll back to the previous configuration. Your current setup will not be affected.</p>
                            </div>
                        </div>
                    )}

                    {stage === 'progress' && job && (
                        <div className="space-y-4">
                            {/* Progress bar */}
                            <div className="space-y-2">
                                <div className="flex justify-between text-sm">
                                    <span className="text-gray-600 dark:text-gray-400">
                                        {PHASE_LABELS[job.progress.phase] || job.progress.phase}
                                    </span>
                                    <span className="text-gray-600 dark:text-gray-400">
                                        {job.progress.percent}%
                                    </span>
                                </div>
                                <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                                    <div 
                                        className="h-full bg-blue-500 transition-all duration-300"
                                        style={{ width: `${job.progress.percent}%` }}
                                    />
                                </div>
                            </div>

                            {/* Time info */}
                            <div className="flex justify-between text-sm text-gray-500 dark:text-gray-400">
                                <span>Elapsed: {formatTime(job.progress.elapsed_seconds)}</span>
                                <span>Est. remaining: {formatTime(Math.max(0, job.progress.estimated_seconds - job.progress.elapsed_seconds))}</span>
                            </div>

                            {/* Status message */}
                            {job.progress.message && (
                                <div className="flex items-center gap-2 text-sm text-blue-600 dark:text-blue-400">
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                    <span className="truncate">{job.progress.message}</span>
                                </div>
                            )}

                            {/* Output log */}
                            <div className="space-y-2">
                                <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                    <Terminal className="w-4 h-4" />
                                    <span>Build Output</span>
                                </div>
                                <div 
                                    ref={outputRef}
                                    className="h-48 bg-gray-900 rounded-lg p-3 overflow-y-auto font-mono text-xs text-gray-300"
                                >
                                    {job.output.map((line, i) => (
                                        <div key={i} className="whitespace-pre-wrap break-all">
                                            {line}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}

                    {stage === 'progress' && !job && (
                        <div className="flex items-center justify-center py-8">
                            <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
                        </div>
                    )}

                    {stage === 'result' && (
                        <div className="space-y-4">
                            {alreadyEnabledMessage ? (
                                <div className="flex items-start gap-3 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg border border-blue-200 dark:border-blue-800">
                                    <CheckCircle2 className="w-5 h-5 text-blue-600 dark:text-blue-400 flex-shrink-0 mt-0.5" />
                                    <div className="text-sm text-blue-800 dark:text-blue-200">
                                        <p className="font-medium">{alreadyEnabledMessage}</p>
                                    </div>
                                </div>
                            ) : job?.completed ? (
                                <div className="flex items-start gap-3 p-4 bg-green-50 dark:bg-green-900/20 rounded-lg border border-green-200 dark:border-green-800">
                                    <CheckCircle2 className="w-5 h-5 text-green-600 dark:text-green-400 flex-shrink-0 mt-0.5" />
                                    <div className="text-sm text-green-800 dark:text-green-200">
                                        <p className="font-medium">{backendDisplayName} backend installed successfully!</p>
                                        <p className="mt-1">You can now use models that require this backend.</p>
                                    </div>
                                </div>
                            ) : (
                                <div className="flex items-start gap-3 p-4 bg-red-50 dark:bg-red-900/20 rounded-lg border border-red-200 dark:border-red-800">
                                    <XCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0 mt-0.5" />
                                    <div className="text-sm text-red-800 dark:text-red-200">
                                        <p className="font-medium">Installation failed</p>
                                        <p className="mt-1">{job?.error || error || 'Unknown error occurred'}</p>
                                        {job?.rolled_back && (
                                            <p className="mt-2 flex items-center gap-1 text-amber-700 dark:text-amber-300">
                                                <RotateCcw className="w-4 h-4" />
                                                Configuration has been rolled back to previous state.
                                            </p>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* Show final output for errors */}
                            {!job?.completed && job?.output && job.output.length > 0 && (
                                <div className="space-y-2">
                                    <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                                        <Terminal className="w-4 h-4" />
                                        <span>Error Log</span>
                                    </div>
                                    <div className="h-32 bg-gray-900 rounded-lg p-3 overflow-y-auto font-mono text-xs text-gray-300">
                                        {job.output.slice(-20).map((line, i) => (
                                            <div key={i} className="whitespace-pre-wrap break-all">
                                                {line}
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Footer */}
                <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-3">
                    {stage === 'confirm' && (
                        <>
                            <button
                                onClick={onClose}
                                className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={startRebuild}
                                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors flex items-center gap-2"
                            >
                                <Terminal className="w-4 h-4" />
                                Enable & Rebuild
                            </button>
                        </>
                    )}

                    {stage === 'progress' && (
                        <p className="text-sm text-gray-500 dark:text-gray-400">
                            Please wait while the backend is being installed...
                        </p>
                    )}

                    {stage === 'result' && (
                        <button
                            onClick={onClose}
                            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
                        >
                            {job?.completed || alreadyEnabledMessage ? 'Done' : 'Close'}
                        </button>
                    )}
                </div>
            </div>
        </div>
    );
};

export default RebuildBackendDialog;
