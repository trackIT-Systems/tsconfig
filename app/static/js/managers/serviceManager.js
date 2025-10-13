// Shared Service Manager - prevents duplicate API calls
export const serviceManager = {
    services: [],
    lastFetch: null,
    fetchPromise: null,
    subscribers: new Set(),
    
    // Subscribe to service updates
    subscribe(callback) {
        this.subscribers.add(callback);
        // Immediately call with current data if available
        if (this.services.length > 0) {
            callback(this.services);
        }
    },
    
    // Unsubscribe from service updates
    unsubscribe(callback) {
        this.subscribers.delete(callback);
    },
    
    // Notify all subscribers
    notify() {
        this.subscribers.forEach(callback => callback(this.services));
    },
    
    // Get services with caching and deduplication
    async getServices(forceRefresh = false) {
        const now = Date.now();
        const cacheValid = this.lastFetch && (now - this.lastFetch) < 5000; // 5 second cache
        
        // Return cached data if valid and not forcing refresh
        if (!forceRefresh && cacheValid && this.services.length > 0) {
            return this.services;
        }
        
        // If there's already a fetch in progress, return that promise
        if (this.fetchPromise) {
            return this.fetchPromise;
        }
        
        // Create and store the fetch promise
        this.fetchPromise = this.fetchServices();
        
        try {
            const services = await this.fetchPromise;
            this.services = services;
            this.lastFetch = now;
            this.notify(); // Notify all subscribers
            return services;
        } finally {
            this.fetchPromise = null;
        }
    },
    
    // Actual API fetch
    async fetchServices() {
        const response = await fetch('/api/systemd/services');
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        return await response.json();
    },
    
    // Find a specific service by name
    findService(serviceName) {
        return this.services.find(service => service.name === serviceName);
    }
};

