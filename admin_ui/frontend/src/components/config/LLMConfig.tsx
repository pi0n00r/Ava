import React from 'react';

interface LLMConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const LLMConfig: React.FC<LLMConfigProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const handlePromptChange = (index: number, field: string, value: any) => {
        const newPrompts = [...(config.prompts || [])];
        newPrompts[index] = { ...newPrompts[index], [field]: value };
        handleChange('prompts', newPrompts);
    };

    const addPrompt = () => {
        handleChange('prompts', [...(config.prompts || []), { role: 'system', content: '' }]);
    };

    const removePrompt = (index: number) => {
        const newPrompts = [...(config.prompts || [])];
        newPrompts.splice(index, 1);
        handleChange('prompts', newPrompts);
    };

    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <div className="flex justify-between items-center">
                    <h3 className="text-lg font-semibold">Default Prompts</h3>
                    <button
                        onClick={addPrompt}
                        className="px-3 py-1 text-sm bg-primary text-primary-foreground rounded hover:bg-primary/90"
                    >
                        Add Prompt
                    </button>
                </div>

                {(config.prompts || []).map((prompt: any, index: number) => (
                    <div key={index} className="border border-border rounded-lg p-4 space-y-2 bg-card">
                        <div className="flex justify-between items-center">
                            <label className="text-sm font-medium">Role</label>
                            <button
                                onClick={() => removePrompt(index)}
                                className="text-destructive hover:text-destructive/80 text-sm"
                            >
                                Remove
                            </button>
                        </div>
                        <select
                            className="w-full p-2 rounded border border-input bg-background"
                            value={prompt.role || 'system'}
                            onChange={(e) => handlePromptChange(index, 'role', e.target.value)}
                        >
                            <option value="system">System</option>
                            <option value="user">User</option>
                            <option value="assistant">Assistant</option>
                        </select>

                        <label className="text-sm font-medium">Content</label>
                        <textarea
                            className="w-full p-2 rounded border border-input bg-background min-h-[100px]"
                            value={prompt.content || ''}
                            onChange={(e) => handlePromptChange(index, 'content', e.target.value)}
                        />
                    </div>
                ))}
            </div>
        </div>
    );
};

export default LLMConfig;
