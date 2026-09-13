import React from 'react';

interface ConfigCardProps {
    children: React.ReactNode;
    className?: string;
}

export const ConfigCard = ({ children, className = '' }: ConfigCardProps) => {
    return (
        <div className={`bg-card border border-border rounded-lg shadow-sm p-6 transition-all duration-200 hover:shadow-md hover:border-border/80 ${className}`}>
            {children}
        </div>
    );
};
