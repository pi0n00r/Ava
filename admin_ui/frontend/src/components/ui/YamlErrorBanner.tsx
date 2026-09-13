import { AlertCircle } from 'lucide-react';

export interface YamlErrorInfo {
    type?: string;
    message?: string;
    line?: number;
    column?: number;
    problem?: string;
    snippet?: string;
}

interface YamlErrorBannerProps {
    error: YamlErrorInfo;
    showSnippet?: boolean;
}

export const YamlErrorBanner = ({ error, showSnippet = true }: YamlErrorBannerProps) => {
    return (
        <div className="bg-red-500/15 border border-red-500/30 text-red-700 dark:text-red-400 p-4 rounded-md space-y-3">
            <div className="flex items-center justify-between">
                <div className="flex items-center font-semibold">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    YAML Configuration Error
                </div>
                <button
                    onClick={() => window.location.reload()}
                    className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-red-500 text-white hover:bg-red-600 font-medium"
                >
                    Reload
                </button>
            </div>
            
            {error.line && (
                <div className="text-sm">
                    <span className="font-medium">Location:</span> Line {error.line}
                    {error.column && <>, Column {error.column}</>}
                </div>
            )}
            
            {error.problem && (
                <div className="text-sm">
                    <span className="font-medium">Problem:</span> {error.problem}
                </div>
            )}
            
            {showSnippet && error.snippet && (
                <div className="mt-2">
                    <span className="font-medium text-sm">Code snippet:</span>
                    <pre className="mt-1 p-3 bg-gray-900 text-gray-100 rounded-md text-xs overflow-x-auto font-mono whitespace-pre">
{error.snippet}
                    </pre>
                </div>
            )}
            
            <div className="text-sm text-red-600 dark:text-red-300 mt-2">
                <strong>How to fix:</strong> Go to <a href="/yaml" className="underline hover:text-red-500">Raw YAML</a> to edit and fix the syntax error, then reload this page.
            </div>
        </div>
    );
};

export default YamlErrorBanner;
