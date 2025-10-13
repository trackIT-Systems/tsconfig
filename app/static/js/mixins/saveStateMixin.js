// Save state management mixin
export function saveStateMixin() {
    return {
        saveState: 'idle', // 'idle', 'saving', 'saved'
        saveRestartState: 'idle', // 'idle', 'saving', 'restarting', 'saved'
        
        setSaveState(state) {
            this.saveState = state;
            if (state === 'saved') {
                setTimeout(() => {
                    this.saveState = 'idle';
                }, 5000);
            }
        },
        
        setSaveRestartState(state) {
            this.saveRestartState = state;
            if (state === 'saved') {
                setTimeout(() => {
                    this.saveRestartState = 'idle';
                }, 5000);
            }
        },
        
        async handleSaveConfig(configSaveFunction) {
            try {
                this.setSaveState('saving');
                await configSaveFunction();
                this.setSaveState('saved');
            } catch (error) {
                this.setSaveState('idle');
                throw error;
            }
        },
        
        async handleSaveAndRestartConfig(configSaveFunction, restartFunction) {
            try {
                this.setSaveRestartState('saving');
                await configSaveFunction();
                this.setSaveRestartState('restarting');
                await restartFunction();
                this.setSaveRestartState('saved');
            } catch (error) {
                this.setSaveRestartState('idle');
                throw error;
            }
        }
    };
}

