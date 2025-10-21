import { apiUrl } from '../utils/apiUtils.js';

export function configUpload() {
    return {
        selectedFile: null,
        uploading: false,
        uploadSuccess: false,
        options: {
            force: false,
            pedantic: false,
            restartServices: false,
            reboot: false
        },

        // Server mode helper
        get serverMode() {
            return window.serverModeManager?.isEnabled() || false;
        },

        init() {
            // Initialize component
            console.log('Config Upload component initialized');
        },

        handleFileChange(event) {
            const file = event.target.files[0];
            if (file) {
                // Validate file type
                if (!file.name.toLowerCase().endsWith('.zip')) {
                    this.showMessage('Please select a ZIP file', 'error');
                    event.target.value = '';
                    return;
                }
                this.selectedFile = file;
                this.uploadSuccess = false;
                console.log('Selected file:', file.name, 'Size:', file.size);
            } else {
                this.selectedFile = null;
            }
        },

        formatFileSize(bytes) {
            if (bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
        },

        async uploadConfig() {
            if (!this.selectedFile) {
                this.showMessage('Please select a file', 'error');
                return;
            }

            // Show confirmation if reboot is enabled
            if (this.options.reboot) {
                if (!confirm('⚠️ Warning: The system will automatically reboot after successfully applying the configuration. This will temporarily interrupt all services and connectivity. Do you want to proceed?')) {
                    return;
                }
            }

            this.uploading = true;
            this.uploadSuccess = false;

            try {
                const formData = new FormData();
                formData.append('file', this.selectedFile);
                formData.append('force', this.options.force.toString());
                formData.append('pedantic', this.options.pedantic.toString());
                formData.append('restart_services', this.options.restartServices.toString());
                // Convert boolean reboot to new format: true -> "force", false -> "allow"
                formData.append('reboot', this.options.reboot ? 'force' : 'allow');

                console.log('Uploading config with options:', {
                    file: this.selectedFile.name,
                    force: this.options.force,
                    pedantic: this.options.pedantic,
                    restartServices: this.options.restartServices,
                    reboot: this.options.reboot
                });

                const url = apiUrl('/api/configs.zip');
                const response = await fetch(url, {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (!response.ok) {
                    throw new Error(data.detail || 'Upload failed');
                }

                console.log('Upload response:', data);
                this.uploadSuccess = data.success;

                if (data.success) {
                    let message = data.message || 'Configuration uploaded successfully';
                    
                    if (data.reboot_initiated) {
                        message += '\n\n⚠️ System is rebooting now. The page will become unavailable shortly.';
                    }
                    
                    this.showMessage(message, 'success');
                } else {
                    this.showMessage(data.message || 'Upload failed.', 'error');
                }

            } catch (error) {
                console.error('Error uploading config:', error);
                this.uploadSuccess = false;
                this.showMessage(error.message || 'Failed to upload configuration', 'error');
            } finally {
                this.uploading = false;
            }
        },

        showMessage(msg, type = 'success') {
            // Use global toast manager
            if (window.toastManager) {
                const title = type === 'success' ? 'Config Upload - Success' : 
                             type === 'error' ? 'Config Upload - Error' : 'Config Upload';
                window.toastManager.show(msg, type, { title });
            } else {
                // Fallback to console if toast manager not available
                console.log(`[Config Upload ${type.toUpperCase()}] ${msg}`);
            }
        }
    };
}

