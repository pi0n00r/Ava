import React, { useState } from 'react';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { Plus, Trash2, Edit2 } from 'lucide-react';

interface ContextsConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const ContextsConfig: React.FC<ContextsConfigProps> = ({ config, onChange }) => {
    const { confirm } = useConfirmDialog();
    const [editingContext, setEditingContext] = useState<string | null>(null);
    const [contextForm, setContextForm] = useState<any>({});
    const [isNewContext, setIsNewContext] = useState(false);

    const availableTools = [
        'blind_transfer',
        'transfer',
        'attended_transfer',
        'cancel_transfer',
        'live_agent_transfer',
        'hangup_call',
        'leave_voicemail',
        'send_email_summary',
        'request_transcript',
        'check_extension_status',
    ];

    const availableProfiles = [
        'default',
        'telephony_responsive',
        'telephony_ulaw_8k',
        'openai_realtime_24k',
        'wideband_pcm_16k'
    ];

    const handleAddContext = () => {
        const defaultTransferTool = availableTools.includes('blind_transfer') ? 'blind_transfer' : 'transfer';
        setEditingContext('new_context');
        setContextForm({
            name: '',
            greeting: 'Hi {caller_name}, how can I help you today?',
            prompt: 'You are a helpful voice assistant.',
            profile: 'telephony_ulaw_8k',
            provider: '',
            tools: [defaultTransferTool, 'hangup_call']
        });
        setIsNewContext(true);
    };

    const handleEditContext = (name: string) => {
        setEditingContext(name);
        setContextForm({ name, ...config[name] });
        setIsNewContext(false);
    };

    const handleSaveContext = () => {
        if (!contextForm.name) return;

        const newContexts = { ...config };
        const { name, ...contextData } = contextForm;

        if (isNewContext && newContexts[name]) {
            toast.error('Context already exists');
            return;
        }

        newContexts[name] = contextData;
        onChange(newContexts);
        setEditingContext(null);
    };

    const handleDeleteContext = async (name: string) => {
        const confirmed = await confirm({
            title: 'Delete Context?',
            description: `Are you sure you want to delete context "${name}"?`,
            confirmText: 'Delete',
            variant: 'destructive'
        });
        if (!confirmed) return;
        const newContexts = { ...config };
        delete newContexts[name];
        onChange(newContexts);
    };

    const handleToolToggle = (tool: string) => {
        const currentTools = contextForm.tools || [];
        const newTools = currentTools.includes(tool)
            ? currentTools.filter((t: string) => t !== tool)
            : [...currentTools, tool];
        setContextForm({ ...contextForm, tools: newTools });
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Conversation Contexts</h3>
                    <p className="text-sm text-muted-foreground">
                        Define different AI personalities and behaviors for various use cases
                    </p>
                </div>
                <button
                    onClick={handleAddContext}
                    className="flex items-center px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    Add Context
                </button>
            </div>

            <div className="grid grid-cols-1 gap-4">
                {Object.entries(config || {}).map(([name, contextData]: [string, any]) => (
                    <div key={name} className="border border-border rounded-lg p-4 bg-card relative group">
                        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex space-x-1">
                            <button
                                onClick={() => handleEditContext(name)}
                                className="p-1 hover:bg-accent rounded"
                            >
                                <Edit2 className="w-4 h-4" />
                            </button>
                            <button
                                onClick={() => handleDeleteContext(name)}
                                className="p-1 hover:bg-destructive/20 text-destructive rounded"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>

                        <h4 className="font-bold text-lg mb-2">{name}</h4>
                        <div className="space-y-2 text-sm">
                            <div>
                                <span className="font-medium">Greeting:</span>
                                <p className="text-muted-foreground truncate">{contextData.greeting}</p>
                            </div>
                            <div>
                                <span className="font-medium">Profile:</span>
                                <span className="ml-2 px-2 py-0.5 rounded text-xs bg-secondary">
                                    {contextData.profile || 'default'}
                                </span>
                            </div>
                            {contextData.provider && (
                                <div>
                                    <span className="font-medium">Provider:</span>
                                    <span className="ml-2 px-2 py-0.5 rounded text-xs bg-secondary">
                                        {contextData.provider}
                                    </span>
                                </div>
                            )}
                            {contextData.tools && contextData.tools.length > 0 && (
                                <div>
                                    <span className="font-medium">Tools:</span>
                                    <div className="flex flex-wrap gap-1 mt-1">
                                        {contextData.tools.map((tool: string) => (
                                            <span key={tool} className="px-2 py-0.5 rounded text-xs bg-accent">
                                                {tool}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                ))}

                {Object.keys(config || {}).length === 0 && (
                    <div className="text-center p-8 text-muted-foreground border border-dashed rounded-lg">
                        No contexts defined. Click "Add Context" to create one.
                    </div>
                )}
            </div>

            {/* Edit Modal */}
            {editingContext && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
                    <div className="bg-card border border-border rounded-lg shadow-lg w-full max-w-3xl max-h-[90vh] overflow-y-auto p-6 space-y-4">
                        <h2 className="text-xl font-bold">
                            {isNewContext ? 'Add Context' : 'Edit Context'}
                        </h2>

                        <div className="space-y-4">
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Context Name</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={contextForm.name}
                                    onChange={(e) => setContextForm({ ...contextForm, name: e.target.value })}
                                    disabled={!isNewContext}
                                    placeholder="e.g., demo_support"
                                />
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium">Greeting</label>
                                <input
                                    type="text"
                                    className="w-full p-2 rounded border border-input bg-background"
                                    value={contextForm.greeting || ''}
                                    onChange={(e) => setContextForm({ ...contextForm, greeting: e.target.value })}
                                    placeholder="Hi {caller_name}, how can I help you?"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Use {'{caller_name}'} as a placeholder for the caller's name
                                </p>
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium">System Prompt</label>
                                <textarea
                                    className="w-full p-2 rounded border border-input bg-background font-mono text-sm min-h-[200px]"
                                    value={contextForm.prompt || ''}
                                    onChange={(e) => setContextForm({ ...contextForm, prompt: e.target.value })}
                                    placeholder="You are a helpful voice assistant..."
                                />
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Audio Profile</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={contextForm.profile || 'telephony_ulaw_8k'}
                                        onChange={(e) => setContextForm({ ...contextForm, profile: e.target.value })}
                                    >
                                        {availableProfiles.map(profile => (
                                            <option key={profile} value={profile}>{profile}</option>
                                        ))}
                                    </select>
                                </div>

                                <div className="space-y-2">
                                    <label className="text-sm font-medium">Provider Override (Optional)</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background"
                                        value={contextForm.provider || ''}
                                        onChange={(e) => setContextForm({ ...contextForm, provider: e.target.value })}
                                        placeholder="Leave empty to use default"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <label className="text-sm font-medium">Available Tools</label>
                                <div className="grid grid-cols-2 gap-2">
                                    {availableTools.map(tool => (
                                        <label key={tool} className="flex items-center space-x-2 p-2 rounded hover:bg-accent cursor-pointer">
                                            <input
                                                type="checkbox"
                                                className="rounded border-input"
                                                checked={(contextForm.tools || []).includes(tool)}
                                                onChange={() => handleToolToggle(tool)}
                                            />
                                            <span className="text-sm">{tool}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>
                        </div>

                        <div className="flex justify-end space-x-2 pt-4 border-t">
                            <button
                                onClick={() => setEditingContext(null)}
                                className="px-4 py-2 rounded border border-input hover:bg-accent"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleSaveContext}
                                className="px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
                            >
                                Save
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default ContextsConfig;
