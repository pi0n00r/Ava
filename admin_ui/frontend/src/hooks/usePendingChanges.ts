import { useState, useEffect } from 'react';

export function usePendingChanges() {
    const [pendingRestart, setPendingRestart] = useState(false);
    const [applyMethod, setApplyMethod] = useState<'hot_reload' | 'restart'>('restart');
    const [applyPlan, setApplyPlan] = useState<any[]>([]);

    useEffect(() => {
        // Read from localStorage on mount
        const storedRestart = localStorage.getItem('yaml_pending_restart');
        const storedMethod = localStorage.getItem('yaml_apply_method');
        const storedPlan = localStorage.getItem('yaml_apply_plan');

        if (storedRestart === 'true') {
            setPendingRestart(true);
        }
        if (storedMethod === 'hot_reload' || storedMethod === 'restart') {
            setApplyMethod(storedMethod as 'hot_reload' | 'restart');
        }
        if (storedPlan) {
            try {
                setApplyPlan(JSON.parse(storedPlan));
            } catch (e) {
                console.error('Failed to parse stored apply plan');
            }
        }
    }, []);

    const setPendingChanges = (method: 'hot_reload' | 'restart' = 'restart', plan: any[] = []) => {
        setPendingRestart(true);
        setApplyMethod(method);
        setApplyPlan(plan);
        localStorage.setItem('yaml_pending_restart', 'true');
        localStorage.setItem('yaml_apply_method', method);
        localStorage.setItem('yaml_apply_plan', JSON.stringify(plan));
    };

    const clearPendingChanges = () => {
        setPendingRestart(false);
        setApplyMethod('restart');
        setApplyPlan([]);
        localStorage.removeItem('yaml_pending_restart');
        localStorage.removeItem('yaml_apply_method');
        localStorage.removeItem('yaml_apply_plan');
    };

    return {
        pendingRestart,
        applyMethod,
        applyPlan,
        setPendingChanges,
        clearPendingChanges,
        // Expose setters slightly modified if individual state manipulation is needed, 
        // though prefer setPendingChanges()
        setPendingRestart: (val: boolean) => {
            setPendingRestart(val);
            if (val) {
                localStorage.setItem('yaml_pending_restart', 'true');
            } else {
                localStorage.removeItem('yaml_pending_restart');
            }
        }
    };
}
