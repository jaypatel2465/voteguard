(function () {
    const LEFT_EYE = [33, 160, 158, 133, 153, 144];
    const RIGHT_EYE = [362, 385, 387, 263, 373, 380];
    const FACE_LEFT = 234;
    const FACE_RIGHT = 454;
    const NOSE_TIP = 1;

    function distance(a, b) {
        return Math.hypot((a.x || 0) - (b.x || 0), (a.y || 0) - (b.y || 0));
    }

    function computeEar(landmarks, indices) {
        const p1 = landmarks[indices[0]];
        const p2 = landmarks[indices[1]];
        const p3 = landmarks[indices[2]];
        const p4 = landmarks[indices[3]];
        const p5 = landmarks[indices[4]];
        const p6 = landmarks[indices[5]];
        const horizontal = distance(p1, p4) || 1;
        const vertical = distance(p2, p6) + distance(p3, p5);
        return vertical / (2 * horizontal);
    }

    function computeYaw(landmarks) {
        const left = landmarks[FACE_LEFT];
        const right = landmarks[FACE_RIGHT];
        const nose = landmarks[NOSE_TIP];
        if (!left || !right || !nose) {
            return 0;
        }
        const width = (right.x || 0) - (left.x || 0);
        if (!width) {
            return 0;
        }
        const midpoint = ((left.x || 0) + (right.x || 0)) / 2;
        return ((nose.x || 0) - midpoint) / width;
    }

    function createCanvas(videoEl) {
        const canvas = document.createElement('canvas');
        canvas.width = videoEl.videoWidth || 640;
        canvas.height = videoEl.videoHeight || 480;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
        return canvas;
    }

    function canvasToBlob(canvas) {
        return new Promise((resolve) => {
            canvas.toBlob((blob) => resolve(blob), 'image/jpeg', 0.9);
        });
    }

    async function waitForVideo(videoEl, timeoutMs = 4000) {
        if (videoEl.readyState >= 2 && videoEl.videoWidth > 0) {
            return;
        }

        await new Promise((resolve, reject) => {
            const onReady = () => {
                cleanup();
                resolve();
            };
            const onTimeout = () => {
                cleanup();
                reject(new Error('Camera preview is not ready yet.'));
            };
            const cleanup = () => {
                clearTimeout(timer);
                videoEl.removeEventListener('loadedmetadata', onReady);
                videoEl.removeEventListener('playing', onReady);
            };

            const timer = setTimeout(onTimeout, timeoutMs);
            videoEl.addEventListener('loadedmetadata', onReady);
            videoEl.addEventListener('playing', onReady);
        });
    }

    function createActiveLiveness(options = {}) {
        const videoEl = document.getElementById(options.videoId);
        const instructionEl = document.getElementById(options.instructionId);
        const statusEl = document.getElementById(options.statusId);
        const countdownEl = document.getElementById(options.countdownId);
        const retryBtn = document.getElementById(options.retryButtonId);
        const actionBtn = document.getElementById(options.actionButtonId);
        const onPassed = typeof options.onPassed === 'function' ? options.onPassed : () => {};
        const onFailed = typeof options.onFailed === 'function' ? options.onFailed : () => {};
        const onError = typeof options.onError === 'function' ? options.onError : () => {};
        const challengePayload = typeof options.challengePayload === 'function' ? options.challengePayload : () => options.challengePayload || null;
        const waitingLabel = options.waitingLabel || 'Complete liveness verification';
        const readyLabel = options.readyLabel || 'Continue';
        const autoPassLabel = options.autoPassLabel || readyLabel;

        let faceMesh = null;
        let animationFrameId = null;
        let processing = false;
        let running = false;
        let passed = false;
        let challenge = null;
        let challengeTimerId = null;
        let frameCapturePending = false;
        let lastFrameCaptureAt = 0;
        let submissionData = null;
        let evidenceFrames = [];
        let yawBaselineSamples = [];
        let baselineYaw = null;
        let maxEar = 0;
        let minEar = 1;
        let blinkCount = 0;
        let eyesClosed = false;
        let direction = null;
        let turnDelta = 0;

        function setActionState(enabled, label) {
            if (!actionBtn) {
                return;
            }
            actionBtn.disabled = !enabled;
            actionBtn.textContent = label;
        }

        function setInstruction(message) {
            if (instructionEl) {
                instructionEl.textContent = message || '';
            }
        }

        function setStatus(message, tone = 'info') {
            if (!statusEl) {
                return;
            }
            statusEl.textContent = message || '';
            statusEl.dataset.state = tone;
        }

        function setCountdown(text) {
            if (countdownEl) {
                countdownEl.textContent = text || '';
            }
        }

        function setRetryVisible(visible) {
            if (retryBtn) {
                retryBtn.classList.toggle('hidden', !visible);
            }
        }

        function resetMetrics() {
            evidenceFrames = [];
            yawBaselineSamples = [];
            baselineYaw = null;
            maxEar = 0;
            minEar = 1;
            blinkCount = 0;
            eyesClosed = false;
            direction = null;
            turnDelta = 0;
            passed = false;
            submissionData = null;
            frameCapturePending = false;
            lastFrameCaptureAt = 0;
        }

        function buildMetrics() {
            return {
                blink_count: blinkCount,
                max_ear: Number(maxEar.toFixed(4)),
                min_ear: Number(minEar.toFixed(4)),
                direction,
                turn_delta: Number(turnDelta.toFixed(4))
            };
        }

        async function captureEvidenceFrame() {
            if (frameCapturePending || !videoEl || videoEl.readyState < 2) {
                return;
            }
            frameCapturePending = true;
            try {
                const canvas = createCanvas(videoEl);
                const blob = await canvasToBlob(canvas);
                if (blob) {
                    evidenceFrames.push(blob);
                    evidenceFrames = evidenceFrames.slice(-4);
                }
            } finally {
                frameCapturePending = false;
            }
        }

        async function ensureFaceMesh() {
            if (faceMesh) {
                return faceMesh;
            }
            if (typeof window.FaceMesh !== 'function') {
                throw new Error('Facial landmarks could not be loaded in this browser.');
            }

            faceMesh = new window.FaceMesh({
                locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
            });
            faceMesh.setOptions({
                maxNumFaces: 1,
                refineLandmarks: true,
                minDetectionConfidence: 0.5,
                minTrackingConfidence: 0.5
            });
            faceMesh.onResults(handleResults);
            return faceMesh;
        }

        function clearTimers() {
            if (animationFrameId) {
                cancelAnimationFrame(animationFrameId);
                animationFrameId = null;
            }
            if (challengeTimerId) {
                clearInterval(challengeTimerId);
                challengeTimerId = null;
            }
        }

        function stop() {
            running = false;
            processing = false;
            clearTimers();
            setCountdown('');
        }

        function markPassed() {
            if (!challenge || passed) {
                return;
            }
            passed = true;
            running = false;
            clearTimers();
            setInstruction('Liveness verified. You can continue.');
            setStatus('Liveness Verified', 'success');
            setCountdown('');
            setRetryVisible(false);
            setActionState(true, readyLabel);
            submissionData = {
                challenge_id: challenge.challenge_id,
                action: challenge.action,
                passed: true,
                completed_at: Date.now() / 1000,
                metrics: buildMetrics()
            };
            onPassed({ challenge, submissionData });
        }

        function fail(message, tone = 'error') {
            stop();
            passed = false;
            submissionData = null;
            setStatus(message, tone);
            setRetryVisible(true);
            setActionState(false, waitingLabel);
            onFailed({ challenge, message });
        }

        function updateCountdown() {
            if (!challenge || !challenge.expires_at) {
                setCountdown('');
                return;
            }
            const seconds = Math.max(0, Math.ceil(challenge.expires_at - (Date.now() / 1000)));
            setCountdown(`Time left: ${seconds}s`);
            if (seconds <= 0 && !passed) {
                fail('Challenge expired. Please try again.');
            }
        }

        function evaluateBlink(ear) {
            maxEar = Math.max(maxEar, ear);
            minEar = Math.min(minEar, ear);
            const openThreshold = Math.max(maxEar * 0.82, 0.22);
            const closedThreshold = Math.max(maxEar * 0.68, 0.16);

            if (maxEar < 0.22) {
                return;
            }

            if (!eyesClosed && ear <= closedThreshold) {
                eyesClosed = true;
                return;
            }

            if (eyesClosed && ear >= openThreshold) {
                blinkCount += 1;
                eyesClosed = false;
            }
        }

        function evaluateTurn(yaw) {
            if (baselineYaw === null) {
                yawBaselineSamples.push(yaw);
                if (yawBaselineSamples.length >= 6) {
                    baselineYaw = yawBaselineSamples.reduce((sum, value) => sum + value, 0) / yawBaselineSamples.length;
                }
                return;
            }

            const delta = yaw - baselineYaw;
            turnDelta = Math.abs(delta);
            if (delta >= 0.08) {
                direction = 'left';
            } else if (delta <= -0.08) {
                direction = 'right';
            } else {
                direction = null;
            }
        }

        function handleResults(results) {
            if (!running || passed || !challenge) {
                return;
            }

            const landmarks = results.multiFaceLandmarks && results.multiFaceLandmarks[0];
            if (!landmarks) {
                setStatus('Align your face inside the frame.', 'warning');
                return;
            }

            setStatus('Detecting...', 'info');
            const now = Date.now();
            if (now - lastFrameCaptureAt >= 250) {
                lastFrameCaptureAt = now;
                captureEvidenceFrame().catch(() => {});
            }

            const leftEar = computeEar(landmarks, LEFT_EYE);
            const rightEar = computeEar(landmarks, RIGHT_EYE);
            const ear = (leftEar + rightEar) / 2;
            const yaw = computeYaw(landmarks);

            evaluateBlink(ear);
            evaluateTurn(yaw);

            if (challenge.action === 'blink' && blinkCount >= 1) {
                markPassed();
                return;
            }

            if (challenge.action === 'turn_left' && direction === 'left' && turnDelta >= 0.08) {
                markPassed();
                return;
            }

            if (challenge.action === 'turn_right' && direction === 'right' && turnDelta >= 0.08) {
                markPassed();
            }
        }

        async function processLoop() {
            if (!running || passed) {
                return;
            }

            if (!videoEl || videoEl.readyState < 2) {
                animationFrameId = requestAnimationFrame(processLoop);
                return;
            }

            if (processing) {
                animationFrameId = requestAnimationFrame(processLoop);
                return;
            }

            processing = true;
            try {
                await faceMesh.send({ image: videoEl });
            } catch (error) {
                fail(error.message || 'Liveness verification failed.');
                return;
            } finally {
                processing = false;
            }

            animationFrameId = requestAnimationFrame(processLoop);
        }

        async function requestChallenge() {
            const payload = challengePayload();
            const response = await fetch(options.challengeUrl, {
                method: 'POST',
                headers: payload ? { 'Content-Type': 'application/json' } : undefined,
                body: payload ? JSON.stringify(payload) : undefined
            });
            const result = await response.json();
            if (!response.ok || !result.success) {
                throw new Error(result.error || 'Could not start liveness verification.');
            }

            if (!result.enabled) {
                challenge = null;
                passed = true;
                submissionData = JSON.stringify({ passed: true, disabled: true });
                setInstruction('Liveness verification is disabled.');
                setStatus('Skipping liveness check.', 'info');
                setRetryVisible(false);
                setActionState(true, autoPassLabel);
                return false;
            }

            challenge = result;
            setInstruction(result.prompt);
            setStatus('Detecting...', 'info');
            setRetryVisible(false);
            setActionState(false, waitingLabel);
            updateCountdown();
            challengeTimerId = setInterval(updateCountdown, 250);
            return true;
        }

        async function start() {
            stop();
            resetMetrics();
            setRetryVisible(false);
            setActionState(false, waitingLabel);

            try {
                await waitForVideo(videoEl);
                await ensureFaceMesh();
                const enabled = await requestChallenge();
                if (!enabled) {
                    return;
                }
                running = true;
                animationFrameId = requestAnimationFrame(processLoop);
            } catch (error) {
                fail(error.message || 'Unable to start liveness detection.');
                onError(error);
            }
        }

        async function restart() {
            await start();
        }

        function getSubmissionData() {
            if (!submissionData) {
                return '';
            }
            return typeof submissionData === 'string' ? submissionData : JSON.stringify(submissionData);
        }

        function getEvidenceFrames() {
            return evidenceFrames.slice();
        }

        function isPassed() {
            return passed;
        }

        if (retryBtn) {
            retryBtn.addEventListener('click', () => {
                restart().catch(() => {});
            });
        }

        return {
            start,
            stop,
            restart,
            isPassed,
            getSubmissionData,
            getEvidenceFrames
        };
    }

    window.VotingSystem = window.VotingSystem || {};
    window.VotingSystem.createActiveLiveness = createActiveLiveness;
})();
