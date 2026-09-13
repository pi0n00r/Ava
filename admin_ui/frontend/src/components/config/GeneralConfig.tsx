import React from 'react';

interface GeneralConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const GeneralConfig: React.FC<GeneralConfigProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                    <label className="text-sm font-medium">Active Pipeline</label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.active_pipeline || ''}
                        onChange={(e) => handleChange('active_pipeline', e.target.value)}
                        placeholder="e.g., default_pipeline"
                    />
                    <p className="text-xs text-muted-foreground">The default pipeline to use for new calls.</p>
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Default Provider</label>
                    <input
                        type="text"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.default_provider || ''}
                        onChange={(e) => handleChange('default_provider', e.target.value)}
                        placeholder="e.g., openai"
                    />
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Config Version</label>
                    <input
                        type="number"
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.config_version || 6}
                        onChange={(e) => handleChange('config_version', parseInt(e.target.value))}
                    />
                </div>

                <div className="space-y-2">
                    <label className="text-sm font-medium">Downstream Mode</label>
                    <select
                        className="w-full p-2 rounded border border-input bg-background"
                        value={config.downstream_mode || 'stream'}
                        onChange={(e) => handleChange('downstream_mode', e.target.value)}
                    >
                        <option value="stream">Stream</option>
                        <option value="file">File</option>
                    </select>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Barge-In Settings</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="barge_in_enabled"
                            className="rounded border-input"
                            checked={config.barge_in?.enabled ?? true}
                            onChange={(e) => handleChange('barge_in', { ...config.barge_in, enabled: e.target.checked })}
                        />
                        <label htmlFor="barge_in_enabled" className="text-sm font-medium">Enable Barge-In</label>
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium">Energy Threshold</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.barge_in?.energy_threshold || 700}
                            onChange={(e) => handleChange('barge_in', { ...config.barge_in, energy_threshold: parseInt(e.target.value) })}
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium">Initial Protection (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.barge_in?.initial_protection_ms || 100}
                            onChange={(e) => handleChange('barge_in', { ...config.barge_in, initial_protection_ms: parseInt(e.target.value) })}
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium">Minimum Duration (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.barge_in?.min_ms || 150}
                            onChange={(e) => handleChange('barge_in', { ...config.barge_in, min_ms: parseInt(e.target.value) })}
                        />
                    </div>

                    <div className="space-y-2">
                        <label className="text-sm font-medium">Post-TTS Protection (ms)</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.barge_in?.post_tts_end_protection_ms || 800}
                            onChange={(e) => handleChange('barge_in', { ...config.barge_in, post_tts_end_protection_ms: parseInt(e.target.value) })}
                        />
                        <p className="text-xs text-muted-foreground">Prevents agent from hearing its own voice tail</p>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default GeneralConfig;
