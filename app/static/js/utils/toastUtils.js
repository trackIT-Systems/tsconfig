/**
 * Centralized Toast Utility for tsconfig
 * Provides a consistent toast notification system across the entire application
 */

class ToastManager {
    constructor() {
        this.toastContainer = null;
        this.toastElement = null;
        this.toastCounter = 0;
        this.init();
    }

    init() {
        // Wait for DOM to be ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => this.setupToast());
        } else {
            this.setupToast();
        }
    }

    setupToast() {
        this.toastContainer = document.getElementById('globalToastContainer');
        this.toastElement = document.getElementById('globalToast');
        
        if (!this.toastContainer || !this.toastElement) {
            console.warn('Global toast elements not found. Toast notifications may not work.');
            return;
        }

        // Set up event listener for toast hidden event to clean up styling
        this.toastElement.addEventListener('hidden.bs.toast', () => {
            this.resetToastStyling();
        });
    }

    /**
     * Show a toast notification
     * @param {string} message - The message to display
     * @param {string} type - Type of toast: 'success', 'error', 'warning', 'info'
     * @param {object} options - Additional options
     * @param {number} options.delay - Auto-hide delay in milliseconds (default: 4000)
     * @param {boolean} options.autohide - Whether to auto-hide (default: true)
     * @param {string} options.title - Custom title (optional)
     */
    show(message, type = 'info', options = {}) {
        if (!this.toastElement) {
            console.warn('Toast system not initialized. Falling back to console log.');
            console.log(`[${type.toUpperCase()}] ${message}`);
            return;
        }

        const {
            delay = 4000,
            autohide = true,
            title = null
        } = options;

        // Update toast content and styling
        this.updateToastContent(message, type, title);
        
        // Configure bootstrap toast options
        const bsToast = new bootstrap.Toast(this.toastElement, {
            autohide: autohide,
            delay: delay
        });

        // Show the toast
        bsToast.show();
    }

    updateToastContent(message, type, customTitle) {
        const toastBody = this.toastElement.querySelector('.toast-body');
        const toastIcon = this.toastElement.querySelector('.toast-icon');
        const toastTitle = this.toastElement.querySelector('.toast-title');

        if (!toastBody || !toastIcon || !toastTitle) {
            console.error('Toast content elements not found');
            return;
        }

        // Reset classes
        this.resetToastStyling();

        // Update content
        toastBody.textContent = message;

        // Configure based on type
        const config = this.getTypeConfig(type);
        
        // Apply styling
        this.toastElement.classList.add(...config.toastClasses);
        toastIcon.className = `toast-icon me-2 fas ${config.iconClass}`;
        toastTitle.textContent = customTitle || config.title;
    }

    getTypeConfig(type) {
        const configs = {
            success: {
                title: 'Success',
                iconClass: 'fa-check-circle text-success',
                toastClasses: ['bg-success', 'text-white']
            },
            error: {
                title: 'Error', 
                iconClass: 'fa-exclamation-triangle text-danger',
                toastClasses: ['bg-danger', 'text-white']
            },
            warning: {
                title: 'Warning',
                iconClass: 'fa-exclamation-triangle text-warning', 
                toastClasses: ['bg-warning', 'text-dark']
            },
            info: {
                title: 'Information',
                iconClass: 'fa-info-circle text-primary',
                toastClasses: ['bg-primary', 'text-white']
            }
        };

        return configs[type] || configs.info;
    }

    resetToastStyling() {
        // Remove all styling classes
        const classesToRemove = [
            'bg-success', 'bg-danger', 'bg-warning', 'bg-primary',
            'text-white', 'text-dark'
        ];
        
        classesToRemove.forEach(cls => {
            this.toastElement.classList.remove(cls);
        });
    }

    // Convenience methods
    success(message, options = {}) {
        this.show(message, 'success', options);
    }

    error(message, options = {}) {
        this.show(message, 'error', { ...options, delay: 6000 }); // Longer delay for errors
    }

    warning(message, options = {}) {
        this.show(message, 'warning', options);
    }

    info(message, options = {}) {
        this.show(message, 'info', options);
    }
}

// Create global instance
window.toastManager = new ToastManager();

// Export for module usage
export { ToastManager };
export default window.toastManager;
