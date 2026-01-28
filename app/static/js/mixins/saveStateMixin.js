// Save state management mixin
import { triggerDeployment, formatDeploymentMessage } from '../utils/deploymentUtils.js';

export function saveStateMixin() {
    return {
        saveState: 'idle', // 'idle', 'saving', 'deploying', 'saved', 'deployed'
        saveRestartState: 'idle', // 'idle', 'saving', 'restarting', 'saved'
        
        setSaveState(state) {
            this.saveState = state;
            if (state === 'saved' || state === 'deployed') {
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
        },
        
        async handleSaveAndDeployConfig(configSaveFunction, configGroupId) {
            console.log('[SaveAndDeploy] Starting save and deploy process');
            console.log('[SaveAndDeploy] Config group ID:', configGroupId);
            console.log('[SaveAndDeploy] Server mode:', window.serverModeManager?.isEnabled());
            
            try {
                // First, save the configuration
                this.setSaveState('saving');
                console.log('[SaveAndDeploy] Saving configuration...');
                await configSaveFunction();
                console.log('[SaveAndDeploy] Configuration saved successfully');
                
                // Show success message for save
                if (window.toastManager) {
                    window.toastManager.success('Configuration saved successfully');
                }
                
                // Then, trigger deployment
                this.setSaveState('deploying');
                console.log('[SaveAndDeploy] Triggering deployment for config group:', configGroupId);
                
                try {
                    const deploymentResult = await triggerDeployment(configGroupId);
                    console.log('[SaveAndDeploy] Deployment result:', deploymentResult);
                    const deploymentMessage = formatDeploymentMessage(deploymentResult);
                    
                    // Show success message for deployment
                    if (window.toastManager) {
                        window.toastManager.success(deploymentMessage);
                    }
                    
                    this.setSaveState('deployed');
                } catch (deployError) {
                    // Deployment failed, but config was saved
                    console.error('[SaveAndDeploy] Deployment error:', deployError);
                    console.error('[SaveAndDeploy] Error details:', {
                        message: deployError.message,
                        stack: deployError.stack
                    });
                    
                    // Show error message for deployment
                    if (window.toastManager) {
                        window.toastManager.error(`Deployment failed: ${deployError.message}`);
                    }
                    
                    // Reset to idle since deployment failed
                    this.setSaveState('idle');
                    
                    // Don't throw - we want to show partial success
                }
            } catch (error) {
                // Config save failed
                console.error('[SaveAndDeploy] Config save failed:', error);
                this.setSaveState('idle');
                throw error;
            }
        }
    };
}

