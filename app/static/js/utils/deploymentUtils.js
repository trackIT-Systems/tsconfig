/**
 * Deployment API Utility
 * Handles triggering deployment to tracker stations via the external deployment API
 */

import { apiUrl } from './apiUtils.js';

/**
 * Trigger deployment for a configuration group
 * @param {string} configGroupId - The ID of the configuration group to deploy
 * @returns {Promise<Object>} Deployment result with station details
 * @throws {Error} If deployment fails
 */
export async function triggerDeployment(configGroupId) {
    console.log('[DeploymentUtils] triggerDeployment called with config group:', configGroupId);
    
    if (!configGroupId) {
        console.error('[DeploymentUtils] No config group ID provided');
        throw new Error('Config group ID is required for deployment');
    }

    // Call the backend proxy endpoint which forwards to the deployment API
    // The deployment API is behind a firewall, so we proxy through our backend
    // Use apiUrl() to ensure the base URL prefix is included (e.g., /tsconfig)
    const deployUrl = apiUrl(`/api/deploy/${configGroupId}`);
    console.log('[DeploymentUtils] Deployment URL:', deployUrl);

    try {
        console.log('[DeploymentUtils] Making POST request to deployment API...');
        const response = await fetch(deployUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        console.log('[DeploymentUtils] Response status:', response.status);
        console.log('[DeploymentUtils] Response ok:', response.ok);

        if (!response.ok) {
            if (response.status === 404) {
                const errorData = await response.json();
                console.error('[DeploymentUtils] 404 error data:', errorData);
                // FastAPI returns errors in 'detail' field
                throw new Error(errorData.detail || errorData.error || `Config group "${configGroupId}" not found in deployment system`);
            }
            
            // Try to get error details from response
            try {
                const errorData = await response.json();
                console.error('[DeploymentUtils] Error response data:', errorData);
                // FastAPI returns errors in 'detail' field
                const errorMessage = errorData.detail || errorData.error || errorData.message || `Deployment failed with status ${response.status}`;
                throw new Error(errorMessage);
            } catch (parseError) {
                // If parseError is our thrown Error, re-throw it
                if (parseError instanceof Error && parseError.message && !parseError.message.includes('JSON')) {
                    throw parseError;
                }
                console.error('[DeploymentUtils] Failed to parse error response:', parseError);
                throw new Error(`Deployment failed with status ${response.status}`);
            }
        }

        const result = await response.json();
        console.log('[DeploymentUtils] Deployment successful, result:', result);
        return result;
    } catch (error) {
        console.error('[DeploymentUtils] Caught error:', error);
        // Handle network errors
        if (error instanceof TypeError && error.message.includes('fetch')) {
            console.error('[DeploymentUtils] Network/fetch error detected');
            throw new Error('Deployment service unavailable. Please check your network connection.');
        }
        throw error;
    }
}

/**
 * Format deployment result message for display
 * @param {Object} result - Deployment result from API
 * @returns {string} Formatted message
 */
export function formatDeploymentMessage(result) {
    // The API already provides a well-formatted message, so just use it
    if (result.message) {
        return result.message;
    }
    
    // Fallback formatting if message is not provided
    if (!result.success) {
        return 'Deployment failed';
    }

    const count = result.deployed_count || 0;
    
    if (count === 0) {
        return 'No active stations found';
    }

    if (count === 1) {
        return 'Deployed to 1 station';
    }

    return `Deployed to ${count} stations`;
}
