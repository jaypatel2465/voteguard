/**
 * Main JavaScript utilities for the Voting System
 * Handles alerts, form validation, and common UI interactions
 */

// Show alert notification
function showAlert(message, type = 'info') {
    // Remove existing alerts
    const existingAlerts = document.querySelectorAll('.floating-alert');
    existingAlerts.forEach(alert => alert.remove());

    // Create alert element
    const alert = document.createElement('div');
    alert.className = `floating-alert alert-${type}`;
    alert.textContent = message;

    // Style the floating alert
    alert.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        z-index: 10000;
        padding: 1rem 1.5rem;
        border-radius: 8px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3);
        animation: slideIn 0.3s ease-out;
        max-width: 400px;
        font-weight: 500;
    `;

    // Add to body
    document.body.appendChild(alert);

    // Auto remove after 5 seconds
    setTimeout(() => {
        alert.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => alert.remove(), 300);
    }, 5000);
}

// Add slide animations to CSS
if (!document.getElementById('dynamic-styles')) {
    const style = document.createElement('style');
    style.id = 'dynamic-styles';
    style.textContent = `
        @keyframes slideIn {
            from {
                transform: translateX(400px);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        
        @keyframes slideOut {
            from {
                transform: translateX(0);
                opacity: 1;
            }
            to {
                transform: translateX(400px);
                opacity: 0;
            }
        }
        
        .floating-alert {
            border-left: 4px solid;
        }
        
        .alert-success {
            background: rgba(16, 185, 129, 0.15);
            border-color: #10b981;
            color: #10b981;
        }
        
        .alert-error {
            background: rgba(239, 68, 68, 0.15);
            border-color: #ef4444;
            color: #ef4444;
        }
        
        .alert-warning {
            background: rgba(245, 158, 11, 0.15);
            border-color: #f59e0b;
            color: #f59e0b;
        }
        
        .alert-info {
            background: rgba(59, 130, 246, 0.15);
            border-color: #3b82f6;
            color: #3b82f6;
        }
    `;
    document.head.appendChild(style);
}

// Form validation helper
function validateForm(formElement) {
    const inputs = formElement.querySelectorAll('input[required], select[required], textarea[required]');
    let isValid = true;

    inputs.forEach(input => {
        if (!input.value.trim()) {
            isValid = false;
            input.style.borderColor = '#ef4444';
        } else {
            input.style.borderColor = '';
        }
    });

    return isValid;
}

// AADHAR validation
function validateAadhar(aadhar) {
    return /^\d{12}$/.test(aadhar);
}

// Phone validation
function validatePhone(phone) {
    return /^\d{10}$/.test(phone);
}

// Email validation
function validateEmail(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

// Handle file input preview
function setupFilePreview(inputId, previewId) {
    const input = document.getElementById(inputId);
    const preview = document.getElementById(previewId);

    if (input && preview) {
        input.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file && file.type.startsWith('image/')) {
                const reader = new FileReader();
                reader.onload = (e) => {
                    preview.innerHTML = `<img src="${e.target.result}" alt="Preview" style="max-width: 200px; border-radius: 8px;">`;
                };
                reader.readAsDataURL(file);
            }
        });
    }
}

// Debounce function for search/filter
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Format date/time
function formatDateTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('en-IN', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Loading spinner
function showLoading(buttonElement, text = 'Loading...') {
    if (buttonElement) {
        buttonElement.disabled = true;
        buttonElement.dataset.originalText = buttonElement.textContent;
        buttonElement.textContent = text;
    }
}

function hideLoading(buttonElement) {
    if (buttonElement && buttonElement.dataset.originalText) {
        buttonElement.disabled = false;
        buttonElement.textContent = buttonElement.dataset.originalText;
    }
}

let bodyScrollLockCount = 0;

function lockBodyScroll() {
    bodyScrollLockCount += 1;
    document.body.classList.add('modal-open');
}

function unlockBodyScroll() {
    bodyScrollLockCount = Math.max(0, bodyScrollLockCount - 1);
    if (bodyScrollLockCount === 0) {
        document.body.classList.remove('modal-open');
    }
}

// Async fetch wrapper with error handling
async function fetchAPI(url, options = {}) {
    try {
        const response = await fetch(url, {
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            },
            ...options
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Request failed');
        }

        return data;
    } catch (error) {
        console.error('API Error:', error);
        throw error;
    }
}

function createQrScanner(options = {}) {
    const readerEl = document.getElementById(options.readerId);
    const videoEl = document.getElementById(options.videoId);
    const statusEl = document.getElementById(options.statusId);
    const startBtn = document.getElementById(options.startButtonId);
    const stopBtn = document.getElementById(options.stopButtonId);
    const fileInput = document.getElementById(options.fileInputId);
    const onDecodedText = typeof options.onDecodedText === 'function' ? options.onDecodedText : (() => {});
    const labels = {
        idle: 'Ready to scan.',
        starting: 'Starting camera...',
        scanning: 'Scanning for a QR code...',
        stopped: 'Scanner stopped.',
        liveUnsupported: 'Live QR scanning is not available in this browser. Upload a QR image or enter the code manually.',
        secureContextRequired: 'Camera access requires HTTPS or localhost. Upload a QR image or enter the code manually.',
        uploading: 'Reading QR image...',
        noCamera: 'No camera was found. Upload a QR image or enter the code manually.',
        ...options.labels
    };

    let html5Scanner = null;
    let fallbackStream = null;
    let barcodeDetector = null;
    let scanFrameHandle = null;
    let currentMode = 'idle';

    function updateStatus(message) {
        if (statusEl) {
            statusEl.textContent = message || '';
        }
    }

    function isLocalhost() {
        return ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
    }

    function resetButtons() {
        if (startBtn) startBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = currentMode === 'idle';
    }

    function showReaderHost(useHtml5Reader) {
        if (readerEl) {
            readerEl.classList.toggle('hidden', !useHtml5Reader);
        }
        if (videoEl) {
            videoEl.classList.toggle('hidden', useHtml5Reader);
        }
    }

    function ensureSecureContextForCamera() {
        if (window.isSecureContext || isLocalhost()) {
            return null;
        }
        return labels.secureContextRequired;
    }

    async function stopHtml5Scanner() {
        if (!html5Scanner) return;
        try {
            await html5Scanner.stop();
        } catch (error) {
            // Ignore stop errors when the scanner is already idle.
        }
        try {
            await html5Scanner.clear();
        } catch (error) {
            // Ignore clear errors when the reader has already been reset.
        }
    }

    function stopFallbackScanner() {
        if (scanFrameHandle) {
            cancelAnimationFrame(scanFrameHandle);
            scanFrameHandle = null;
        }
        if (fallbackStream) {
            fallbackStream.getTracks().forEach((track) => track.stop());
            fallbackStream = null;
        }
        if (videoEl) {
            videoEl.pause();
            videoEl.srcObject = null;
        }
    }

    async function stop() {
        await stopHtml5Scanner();
        stopFallbackScanner();
        currentMode = 'idle';
        resetButtons();
        updateStatus(labels.stopped);
    }

    async function handleDecodedText(decodedText) {
        await stop();
        onDecodedText(decodedText);
    }

    async function startWithHtml5Qrcode() {
        if (!window.Html5Qrcode) {
            return false;
        }

        if (!readerEl) {
            updateStatus(labels.liveUnsupported);
            return false;
        }

        showReaderHost(true);
        currentMode = 'html5-qrcode';
        html5Scanner = html5Scanner || new Html5Qrcode(options.readerId, { verbose: false });

        let cameras = [];
        try {
            cameras = await Html5Qrcode.getCameras();
        } catch (error) {
            throw error;
        }

        if (!Array.isArray(cameras) || cameras.length === 0) {
            updateStatus(labels.noCamera);
            currentMode = 'idle';
            resetButtons();
            return true;
        }

        await html5Scanner.start(
            { facingMode: 'environment' },
            { fps: 10, qrbox: { width: 240, height: 240 } },
            (decodedText) => {
                handleDecodedText(decodedText);
            },
            () => {}
        );

        updateStatus(labels.scanning);
        resetButtons();
        return true;
    }

    async function startWithBarcodeDetector() {
        if (!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia) {
            updateStatus(labels.liveUnsupported);
            currentMode = 'idle';
            resetButtons();
            return false;
        }

        showReaderHost(false);
        currentMode = 'barcode-detector';
        barcodeDetector = barcodeDetector || new BarcodeDetector({ formats: ['qr_code'] });

        fallbackStream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'environment' },
            audio: false
        });

        if (videoEl) {
            videoEl.srcObject = fallbackStream;
            await videoEl.play();
        }

        const scanLoop = async () => {
            if (currentMode !== 'barcode-detector' || !barcodeDetector || !videoEl) {
                return;
            }

            try {
                const results = await barcodeDetector.detect(videoEl);
                if (results.length > 0) {
                    await handleDecodedText(results[0].rawValue);
                    return;
                }
            } catch (error) {
                console.error('BarcodeDetector scan failed:', error);
            }

            scanFrameHandle = requestAnimationFrame(scanLoop);
        };

        updateStatus(labels.scanning);
        resetButtons();
        scanLoop();
        return true;
    }

    async function start() {
        if (currentMode !== 'idle') {
            return;
        }

        const secureContextError = ensureSecureContextForCamera();
        if (secureContextError) {
            updateStatus(secureContextError);
            resetButtons();
            return;
        }

        updateStatus(labels.starting);
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;

        try {
            const startedWithHtml5 = await startWithHtml5Qrcode();
            if (!startedWithHtml5) {
                await startWithBarcodeDetector();
            }
        } catch (error) {
            console.error('QR scanner start failed:', error);
            currentMode = 'idle';
            resetButtons();

            const message = String(error && error.message ? error.message : error);
            if (/permission|denied|notallowed/i.test(message)) {
                updateStatus('Camera permission was denied. Upload a QR image or enter the code manually.');
            } else if (/notfound|no camera/i.test(message)) {
                updateStatus(labels.noCamera);
            } else {
                updateStatus('Unable to start the camera. Upload a QR image or enter the code manually.');
            }
        }
    }

    async function scanFileWithBarcodeDetector(file) {
        if (!('BarcodeDetector' in window) || typeof createImageBitmap !== 'function') {
            throw new Error('Image QR scanning is not supported in this browser.');
        }

        barcodeDetector = barcodeDetector || new BarcodeDetector({ formats: ['qr_code'] });
        const bitmap = await createImageBitmap(file);
        const results = await barcodeDetector.detect(bitmap);
        if (!results.length) {
            throw new Error('No QR code detected in the uploaded image.');
        }
        return results[0].rawValue;
    }

    async function scanFile(file) {
        if (!file) {
            return;
        }

        await stop();
        updateStatus(labels.uploading);

        try {
            let decodedText = null;

            if (window.Html5Qrcode && readerEl) {
                showReaderHost(true);
                html5Scanner = html5Scanner || new Html5Qrcode(options.readerId, { verbose: false });
                decodedText = await html5Scanner.scanFile(file, true);
                await html5Scanner.clear();
            } else {
                showReaderHost(false);
                decodedText = await scanFileWithBarcodeDetector(file);
            }

            await handleDecodedText(decodedText);
        } catch (error) {
            console.error('QR image scan failed:', error);
            updateStatus(String(error && error.message ? error.message : error));
            showAlert(String(error && error.message ? error.message : 'Unable to read the uploaded QR image.'), 'error');
            currentMode = 'idle';
            resetButtons();
        } finally {
            if (fileInput) {
                fileInput.value = '';
            }
        }
    }

    if (startBtn) {
        startBtn.addEventListener('click', start);
    }

    if (stopBtn) {
        stopBtn.addEventListener('click', stop);
        stopBtn.disabled = true;
    }

    if (fileInput) {
        fileInput.addEventListener('change', async (event) => {
            const [file] = event.target.files || [];
            await scanFile(file);
        });
    }

    return {
        start,
        stop,
        scanFile,
        updateStatus
    };
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    // Add focus effects to inputs
    const inputs = document.querySelectorAll('input, select, textarea');
    inputs.forEach(input => {
        input.addEventListener('focus', () => {
            input.parentElement?.classList.add('focused');
        });
        input.addEventListener('blur', () => {
            input.parentElement?.classList.remove('focused');
        });
    });

    // Add ripple effect to buttons
    const buttons = document.querySelectorAll('.btn');
    buttons.forEach(button => {
        button.addEventListener('click', function (e) {
            const ripple = document.createElement('span');
            const rect = this.getBoundingClientRect();
            const size = Math.max(rect.width, rect.height);
            const x = e.clientX - rect.left - size / 2;
            const y = e.clientY - rect.top - size / 2;

            ripple.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                border-radius: 50%;
                background: rgba(255, 255, 255, 0.3);
                top: ${y}px;
                left: ${x}px;
                pointer-events: none;
                animation: ripple 0.6s ease-out;
            `;

            this.style.position = 'relative';
            this.style.overflow = 'hidden';
            this.appendChild(ripple);

            setTimeout(() => ripple.remove(), 600);
        });
    });

    // Add ripple animation
    if (!document.getElementById('ripple-animation')) {
        const style = document.createElement('style');
        style.id = 'ripple-animation';
        style.textContent = `
            @keyframes ripple {
                from {
                    transform: scale(0);
                    opacity: 1;
                }
                to {
                    transform: scale(2);
                    opacity: 0;
                }
            }
        `;
        document.head.appendChild(style);
    }
});

// Export functions for use in other scripts
window.VotingSystem = {
    showAlert,
    validateForm,
    validateAadhar,
    validatePhone,
    validateEmail,
    setupFilePreview,
    debounce,
    formatDateTime,
    showLoading,
    hideLoading,
    fetchAPI,
    lockBodyScroll,
    unlockBodyScroll,
    createQrScanner
};
