// Save state management mixin
import { triggerDeployment, formatDeploymentMessage } from '../utils/deploymentUtils.js';

export function saveStateMixin() {
    return {
        saveState: 'idle', // 'idle', 'saving', 'saved'
        saveRestartState: 'idle', // 'idle', 'saving', 'restarting', 'saved'
        deployState: 'idle', // 'idle', 'deploying', 'deployed'
        
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
        
        setDeployState(state) {
            this.deployState = state;
            if (state === 'deployed') {
                setTimeout(() => {
                    this.deployState = 'idle';
                }, 5000);
            }
        },
        
        async handleSaveConfig(configSaveFunction) {
            try {
                this.setSaveState('saving');
                await configSaveFunction();
                this.setSaveState('saved');
                // In server mode, show a toast (tracker mode components show their own messages)
                if (window.serverModeManager?.isEnabled() && window.toastManager) {
                    window.toastManager.success('Configuration saved successfully');
                }
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
        },
        
        async handleDeployConfig(configGroupId) {
            console.log('[Deploy] Starting deploy-only process');
            console.log('[Deploy] Config group ID:', configGroupId);
            console.log('[Deploy] Server mode:', window.serverModeManager?.isEnabled());
            
            if (!configGroupId) {
                console.error('[Deploy] No config group ID provided');
                throw new Error('Config group ID is required for deployment');
            }
            
            try {
                this.setDeployState('deploying');
                console.log('[Deploy] Triggering deployment for config group:', configGroupId);
                
                const deploymentResult = await triggerDeployment(configGroupId);
                console.log('[Deploy] Deployment result:', deploymentResult);
                const deploymentMessage = formatDeploymentMessage(deploymentResult);
                
                if (window.toastManager) {
                    window.toastManager.success(deploymentMessage);
                }
                
                this.setDeployState('deployed');
            } catch (deployError) {
                console.error('[Deploy] Deployment error:', deployError);
                console.error('[Deploy] Error details:', {
                    message: deployError.message,
                    stack: deployError.stack
                });
                
                if (window.toastManager) {
                    window.toastManager.error(`Deployment failed: ${deployError.message}`);
                }
                
                this.setDeployState('idle');
                throw deployError;
            }
        }
    };
}
