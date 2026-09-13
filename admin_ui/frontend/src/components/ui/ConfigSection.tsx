import React from 'react';

interface ConfigSectionProps {
    title: string;
    description?: string;
    children: React.ReactNode;
}

export const ConfigSection = ({ title, description, children }: ConfigSectionProps) => {
    return (
        <div className="mb-6">
            <div className="mb-3">
                <h3 className="text-lg font-semibold tracking-tight">
                    {title}
                </h3>
                {description && (
                    <p className="text-sm text-muted-foreground mt-1">
                        {description}
                    </p>
                )}
            </div>
            <div className="space-y-4">
                {children}
            </div>
        </div>
    );
};
