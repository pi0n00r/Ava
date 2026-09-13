import React from 'react';
import { AlertCircle, XCircle, CheckCircle, Info } from 'lucide-react';

interface ErrorPanelProps {
    type?: 'error' | 'warning' | 'success' | 'info';
    title?: string;
    message: string;
    details?: string;
    onDismiss?: () => void;
    className?: string;
}

const ErrorPanel: React.FC<ErrorPanelProps> = ({
    type = 'error',
    title,
    message,
    details,
    onDismiss,
    className = ''
}) => {
    const config = {
        error: {
            icon: XCircle,
            bgColor: 'bg-destructive/10',
            borderColor: 'border-destructive/50',
            textColor: 'text-destructive',
            iconColor: 'text-destructive',
            defaultTitle: 'Error'
        },
        warning: {
            icon: AlertCircle,
            bgColor: 'bg-yellow-500/10',
            borderColor: 'border-yellow-500/50',
            textColor: 'text-yellow-600 dark:text-yellow-400',
            iconColor: 'text-yellow-600 dark:text-yellow-400',
            defaultTitle: 'Warning'
        },
        success: {
            icon: CheckCircle,
            bgColor: 'bg-green-500/10',
            borderColor: 'border-green-500/50',
            textColor: 'text-green-600 dark:text-green-400',
            iconColor: 'text-green-600 dark:text-green-400',
            defaultTitle: 'Success'
        },
        info: {
            icon: Info,
            bgColor: 'bg-blue-500/10',
            borderColor: 'border-blue-500/50',
            textColor: 'text-blue-600 dark:text-blue-400',
            iconColor: 'text-blue-600 dark:text-blue-400',
            defaultTitle: 'Information'
        }
    };

    const { icon: Icon, bgColor, borderColor, textColor, iconColor, defaultTitle } = config[type];

    return (
        <div className={`rounded-lg border p-4 ${bgColor} ${borderColor} ${className}`}>
            <div className="flex items-start gap-3">
                <Icon className={`w-5 h-5 ${iconColor} flex-shrink-0 mt-0.5`} />
                <div className="flex-1">
                    {(title || defaultTitle) && (
                        <h4 className={`font-semibold ${textColor} mb-1`}>
                            {title || defaultTitle}
                        </h4>
                    )}
                    <p className={`text-sm ${textColor}`}>{message}</p>
                    {details && (
                        <details className="mt-2">
                            <summary className={`text-xs cursor-pointer ${textColor} opacity-75 hover:opacity-100`}>
                                Technical details
                            </summary>
                            <pre className={`mt-1 text-xs ${textColor} opacity-75 overflow-x-auto p-2 rounded bg-black/10`}>
                                {details}
                            </pre>
                        </details>
                    )}
                </div>
                {onDismiss && (
                    <button
                        onClick={onDismiss}
                        className={`${textColor} opacity-75 hover:opacity-100 transition-opacity`}
                        aria-label="Dismiss"
                    >
                        <XCircle className="w-4 h-4" />
                    </button>
                )}
            </div>
        </div>
    );
};

export default ErrorPanel;
