import React, { useState } from 'react';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { Plus, Trash2, Edit2, ArrowRight, MoveUp, MoveDown } from 'lucide-react';

interface PipelinesConfigProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const PipelinesConfig: React.FC<PipelinesConfigProps> = ({ config, onChange }) => {
    const { confirm } = useConfirmDialog();
    const [editingPipeline, setEditingPipeline] = useState<string | null>(null);
    const [pipelineForm, setPipelineForm] = useState<any>({});
    const [isNewPipeline, setIsNewPipeline] = useState(false);

    const handleEditPipeline = (name: string) => {
        setEditingPipeline(name);
        setPipelineForm({ name, ...config[name] });
        setIsNewPipeline(false);
    };

    const handleAddPipeline = () => {
        setEditingPipeline('new_pipeline');
        setPipelineForm({
            name: '',
            input: { provider: 'local', format: 'slin', sample_rate: 8000 },
            processors: [],
            output: { provider: 'local', format: 'slin', sample_rate: 8000 }
        });
        setIsNewPipeline(true);
    };

    const handleSavePipeline = () => {
        if (!pipelineForm.name) return;

        const newPipelines = { ...config };
        const { name, ...pipelineData } = pipelineForm;

        if (isNewPipeline && newPipelines[name]) {
            toast.error('Pipeline already exists');
            return;
        }

        newPipelines[name] = pipelineData;
        onChange(newPipelines);
        setEditingPipeline(null);
    };

    const handleDeletePipeline = async (name: string) => {
        const confirmed = await confirm({
            title: 'Delete Pipeline?',
            description: `Are you sure you want to delete pipeline "${name}"?`,
            confirmText: 'Delete',
            variant: 'destructive'
        });
        if (!confirmed) return;
        const newPipelines = { ...config };
        delete newPipelines[name];
        onChange(newPipelines);
    };

    // Processor Management
    const addProcessor = () => {
        const newProcessors = [...(pipelineForm.processors || [])];
        newProcessors.push({ type: 'stt', provider: 'local' });
        setPipelineForm({ ...pipelineForm, processors: newProcessors });
    };

    const removeProcessor = (index: number) => {
        const newProcessors = [...(pipelineForm.processors || [])];
        newProcessors.splice(index, 1);
        setPipelineForm({ ...pipelineForm, processors: newProcessors });
    };

    const updateProcessor = (index: number, field: string, value: any) => {
        const newProcessors = [...(pipelineForm.processors || [])];
        newProcessors[index] = { ...newProcessors[index], [field]: value };
        setPipelineForm({ ...pipelineForm, processors: newProcessors });
    };

    const moveProcessor = (index: number, direction: 'up' | 'down') => {
        const newProcessors = [...(pipelineForm.processors || [])];
        if (direction === 'up' && index > 0) {
            [newProcessors[index], newProcessors[index - 1]] = [newProcessors[index - 1], newProcessors[index]];
        } else if (direction === 'down' && index < newProcessors.length - 1) {
            [newProcessors[index], newProcessors[index + 1]] = [newProcessors[index + 1], newProcessors[index]];
        }
        setPipelineForm({ ...pipelineForm, processors: newProcessors });
    };

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Processing Pipelines</h3>
                    <p className="text-sm text-muted-foreground">
                        Define data flow pipelines (Input → Processors → Output)
                    </p>
                </div>
                <button
                    onClick={handleAddPipeline}
                    className="flex items-center px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
                >
                    <Plus className="w-4 h-4 mr-2" />
                    Add Pipeline
                </button>
            </div>

            <div className="grid grid-cols-1 gap-4">
                {Object.entries(config || {}).map(([name, pipeline]: [string, any]) => (
                    <div key={name} className="border border-border rounded-lg p-4 bg-card relative group">
                        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity flex space-x-1">
                            <button onClick={() => handleEditPipeline(name)} className="p-1 hover:bg-accent rounded">
                                <Edit2 className="w-4 h-4" />
                            </button>
                            <button onClick={() => handleDeletePipeline(name)} className="p-1 hover:bg-destructive/20 text-destructive rounded">
                                <Trash2 className="w-4 h-4" />
                            </button>
                        </div>

                        <h4 className="font-bold text-lg mb-4">{name}</h4>

                        <div className="flex items-center space-x-2 text-sm overflow-x-auto pb-2">
                            {/* Input Node */}
                            <div className="flex flex-col items-center p-2 bg-secondary rounded min-w-[100px]">
                                <span className="font-semibold">Input</span>
                                <span className="text-xs text-muted-foreground">{pipeline.input?.provider || 'default'}</span>
                            </div>

                            <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />

                            {/* Processors */}
                            {pipeline.processors?.map((proc: any, idx: number) => (
                                <React.Fragment key={idx}>
                                    <div className="flex flex-col items-center p-2 bg-accent rounded min-w-[100px] border border-accent-foreground/20">
                                        <span className="font-semibold uppercase">{proc.type}</span>
                                        <span className="text-xs text-muted-foreground">{proc.provider}</span>
                                    </div>
                                    <ArrowRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                                </React.Fragment>
                            ))}

                            {/* Output Node */}
                            <div className="flex flex-col items-center p-2 bg-secondary rounded min-w-[100px]">
                                <span className="font-semibold">Output</span>
                                <span className="text-xs text-muted-foreground">{pipeline.output?.provider || 'default'}</span>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            {/* Edit Modal */}
            {editingPipeline && (
                <div className="fixed inset-0 bg-background/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
                    <div className="bg-card border border-border rounded-lg shadow-lg w-full max-w-4xl max-h-[90vh] overflow-y-auto p-6 space-y-6">
                        <h2 className="text-xl font-bold">
                            {isNewPipeline ? 'Add Pipeline' : 'Edit Pipeline'}
                        </h2>

                        <div className="space-y-2">
                            <label className="text-sm font-medium">Pipeline Name</label>
                            <input
                                type="text"
                                className="w-full p-2 rounded border border-input bg-background"
                                value={pipelineForm.name}
                                onChange={(e) => setPipelineForm({ ...pipelineForm, name: e.target.value })}
                                disabled={!isNewPipeline}
                                placeholder="e.g., local_hybrid"
                            />
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                            {/* Input Config */}
                            <div className="space-y-4 border p-4 rounded-lg">
                                <h4 className="font-semibold text-sm border-b pb-2">Input Source</h4>
                                <div className="space-y-2">
                                    <label className="text-xs font-medium">Provider</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background text-sm"
                                        value={pipelineForm.input?.provider || ''}
                                        onChange={(e) => setPipelineForm({ ...pipelineForm, input: { ...pipelineForm.input, provider: e.target.value } })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-xs font-medium">Format</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background text-sm"
                                        value={pipelineForm.input?.format || 'slin'}
                                        onChange={(e) => setPipelineForm({ ...pipelineForm, input: { ...pipelineForm.input, format: e.target.value } })}
                                    >
                                        <option value="slin">slin</option>
                                        <option value="ulaw">ulaw</option>
                                        <option value="alaw">alaw</option>
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-xs font-medium">Sample Rate</label>
                                    <input
                                        type="number"
                                        className="w-full p-2 rounded border border-input bg-background text-sm"
                                        value={pipelineForm.input?.sample_rate || 8000}
                                        onChange={(e) => setPipelineForm({ ...pipelineForm, input: { ...pipelineForm.input, sample_rate: parseInt(e.target.value) } })}
                                    />
                                </div>
                            </div>

                            {/* Processors Config */}
                            <div className="space-y-4 border p-4 rounded-lg md:col-span-2">
                                <div className="flex justify-between items-center border-b pb-2">
                                    <h4 className="font-semibold text-sm">Processors Chain</h4>
                                    <button onClick={addProcessor} className="text-xs flex items-center bg-secondary px-2 py-1 rounded hover:bg-secondary/80">
                                        <Plus className="w-3 h-3 mr-1" /> Add Step
                                    </button>
                                </div>

                                <div className="space-y-2">
                                    {pipelineForm.processors?.map((proc: any, idx: number) => (
                                        <div key={idx} className="flex items-center space-x-2 bg-accent/50 p-2 rounded">
                                            <div className="flex flex-col space-y-1">
                                                <button onClick={() => moveProcessor(idx, 'up')} disabled={idx === 0} className="p-1 hover:bg-background rounded disabled:opacity-30">
                                                    <MoveUp className="w-3 h-3" />
                                                </button>
                                                <button onClick={() => moveProcessor(idx, 'down')} disabled={idx === (pipelineForm.processors.length - 1)} className="p-1 hover:bg-background rounded disabled:opacity-30">
                                                    <MoveDown className="w-3 h-3" />
                                                </button>
                                            </div>

                                            <div className="flex-1 grid grid-cols-2 gap-2">
                                                <select
                                                    className="p-1 rounded border border-input bg-background text-sm"
                                                    value={proc.type}
                                                    onChange={(e) => updateProcessor(idx, 'type', e.target.value)}
                                                >
                                                    <option value="stt">STT</option>
                                                    <option value="llm">LLM</option>
                                                    <option value="tts">TTS</option>
                                                </select>
                                                <input
                                                    type="text"
                                                    className="p-1 rounded border border-input bg-background text-sm"
                                                    value={proc.provider}
                                                    onChange={(e) => updateProcessor(idx, 'provider', e.target.value)}
                                                    placeholder="Provider Name"
                                                />
                                            </div>

                                            <button onClick={() => removeProcessor(idx)} className="p-1 hover:bg-destructive/20 text-destructive rounded">
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    ))}
                                    {(!pipelineForm.processors || pipelineForm.processors.length === 0) && (
                                        <div className="text-center text-sm text-muted-foreground py-4">
                                            No processors defined. Add steps to process audio.
                                        </div>
                                    )}
                                </div>
                            </div>

                            {/* Output Config */}
                            <div className="space-y-4 border p-4 rounded-lg">
                                <h4 className="font-semibold text-sm border-b pb-2">Output Destination</h4>
                                <div className="space-y-2">
                                    <label className="text-xs font-medium">Provider</label>
                                    <input
                                        type="text"
                                        className="w-full p-2 rounded border border-input bg-background text-sm"
                                        value={pipelineForm.output?.provider || ''}
                                        onChange={(e) => setPipelineForm({ ...pipelineForm, output: { ...pipelineForm.output, provider: e.target.value } })}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <label className="text-xs font-medium">Format</label>
                                    <select
                                        className="w-full p-2 rounded border border-input bg-background text-sm"
                                        value={pipelineForm.output?.format || 'slin'}
                                        onChange={(e) => setPipelineForm({ ...pipelineForm, output: { ...pipelineForm.output, format: e.target.value } })}
                                    >
                                        <option value="slin">slin</option>
                                        <option value="ulaw">ulaw</option>
                                        <option value="alaw">alaw</option>
                                    </select>
                                </div>
                                <div className="space-y-2">
                                    <label className="text-xs font-medium">Sample Rate</label>
                                    <input
                                        type="number"
                                        className="w-full p-2 rounded border border-input bg-background text-sm"
                                        value={pipelineForm.output?.sample_rate || 8000}
                                        onChange={(e) => setPipelineForm({ ...pipelineForm, output: { ...pipelineForm.output, sample_rate: parseInt(e.target.value) } })}
                                    />
                                </div>
                            </div>
                        </div>

                        <div className="flex justify-end space-x-2 pt-4 border-t">
                            <button
                                onClick={() => setEditingPipeline(null)}
                                className="px-4 py-2 rounded border border-input hover:bg-accent"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleSavePipeline}
                                className="px-4 py-2 rounded bg-primary text-primary-foreground hover:bg-primary/90"
                            >
                                Save Pipeline
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default PipelinesConfig;
