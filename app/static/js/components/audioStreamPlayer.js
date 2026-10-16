import { compose } from '../mixins/compose.js';
import { lifecycleMixin } from '../mixins/lifecycleMixin.js';

/**
 * The live audio player of soundscapepipe (/stream.mp3), on the status page
 * and in the soundscapepipe settings. It checks every 5 s whether the stream
 * is up, and disconnects the stream 5 s after the player is paused, so a
 * forgotten tab does not keep a connection (and its bandwidth) open.
 *
 * @param {() => boolean} serviceInactive - Whether the soundscapepipe unit is
 *     known to be inactive; there is no stream then, so it is not asked for one.
 *     The template passes a closure over its own scope.
 */
export function audioStreamPlayer(serviceInactive = () => false) {
    return compose(lifecycleMixin(), {
        streamAvailable: false,
        checkingStream: true,
        pauseTimer: null,
        audioConnected: true,

        init() {
            this.checkStreamAvailability(true, true);
            // Periodic checks do not show the "checking" state, to avoid flicker
            this.every(5000, () => this.checkStreamAvailability(false, false));
            this.onDestroy(() => {
                if (this.pauseTimer) {
                    clearTimeout(this.pauseTimer);
                    this.pauseTimer = null;
                }
            });
        },

        /**
         * @param {boolean} force - Ask even if the service is known to be inactive
         * @param {boolean} showChecking - Show the "checking" state meanwhile
         */
        async checkStreamAvailability(force = false, showChecking = true) {
            if (!force && serviceInactive()) {
                this.streamAvailable = false;
                this.checkingStream = false;
                return;
            }
            if (showChecking) {
                this.checkingStream = true;
            }
            try {
                const response = await fetch('/stream.mp3', {
                    method: 'HEAD',
                    cache: 'no-cache',
                });
                this.streamAvailable = response.ok;
            } catch (error) {
                this.streamAvailable = false;
            }
            this.checkingStream = false;
        },

        // Start the 5-second timer to disconnect
        handleAudioPause() {
            this.pauseTimer = setTimeout(() => {
                this.disconnectAudio();
            }, 5000);
        },

        // Clear the disconnect timer and reconnect if needed
        handleAudioPlay() {
            if (this.pauseTimer) {
                clearTimeout(this.pauseTimer);
                this.pauseTimer = null;
            }
            if (!this.audioConnected) {
                // Prevent the current play attempt and reconnect
                const audio = this.$refs.audioPlayer;
                if (audio) {
                    audio.pause();
                }
                this.reconnectAudio();
            }
        },

        disconnectAudio() {
            const audio = this.$refs.audioPlayer;
            if (audio) {
                audio.pause();
                this.audioConnected = false;
            }
        },

        reconnectAudio() {
            const audio = this.$refs.audioPlayer;
            if (audio) {
                // Force reload of the stream by updating the src
                audio.src = '/stream.mp3?' + Date.now();
                audio.load();
                this.audioConnected = true;
                // Auto-play since the user clicked play to reconnect
                setTimeout(() => {
                    audio.play().catch(() => {});
                }, 100);
            }
        },
    });
}
