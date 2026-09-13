import React from 'react';

interface AsteriskConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const AsteriskConfig: React.FC<AsteriskConfigProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const handleNestedChange = (parent: string, field: string, value: any) => {
        onChange({
            ...config,
            [parent]: {
                ...config[parent],
                [field]: value
            }
        });
    };

    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Network Settings</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Application Name</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.app_name || 'asterisk-ai-voice-agent'}
                            onChange={(e) => handleChange('app_name', e.target.value)}
                            placeholder="asterisk-ai-voice-agent"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">ARI URL</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.network?.ari_url || ''}
                            onChange={(e) => handleNestedChange('network', 'ari_url', e.target.value)}
                            placeholder="http://localhost:8088/ari"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">ARI Application</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.network?.ari_app || ''}
                            onChange={(e) => handleNestedChange('network', 'ari_app', e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Username</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.network?.username || ''}
                            onChange={(e) => handleNestedChange('network', 'username', e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Password</label>
                        <input
                            type="password"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.network?.password || ''}
                            onChange={(e) => handleNestedChange('network', 'password', e.target.value)}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Inbound Call Handling</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="inbound_enabled"
                            className="rounded border-input"
                            checked={config.inbound?.enabled ?? true}
                            onChange={(e) => handleNestedChange('inbound', 'enabled', e.target.checked)}
                        />
                        <label htmlFor="inbound_enabled" className="text-sm font-medium">Enable Inbound Handling</label>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Default Context</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.inbound?.default_context || 'default'}
                            onChange={(e) => handleNestedChange('inbound', 'default_context', e.target.value)}
                        />
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Outbound Call Handling</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            id="outbound_enabled"
                            className="rounded border-input"
                            checked={config.outbound?.enabled ?? true}
                            onChange={(e) => handleNestedChange('outbound', 'enabled', e.target.checked)}
                        />
                        <label htmlFor="outbound_enabled" className="text-sm font-medium">Enable Outbound Handling</label>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Context</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.outbound?.context || 'from-internal'}
                            onChange={(e) => handleNestedChange('outbound', 'context', e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Extension</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.outbound?.extension || ''}
                            onChange={(e) => handleNestedChange('outbound', 'extension', e.target.value)}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Priority</label>
                        <input
                            type="number"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.outbound?.priority || 1}
                            onChange={(e) => handleNestedChange('outbound', 'priority', parseInt(e.target.value))}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
};

export default AsteriskConfig;
