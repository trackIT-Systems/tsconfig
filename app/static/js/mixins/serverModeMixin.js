import { serverModeManager } from '../managers/serverModeManager.js';

/** `serverMode` for templates (x-show="!serverMode"): whether this is the server, not a tracker. */
export function serverModeMixin() {
    return {
        get serverMode() {
            return serverModeManager.isEnabled();
        },
    };
}
