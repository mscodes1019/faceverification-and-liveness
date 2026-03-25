(function () {
  'use strict';

  var API      = window.location.origin;
  var personId = new URLSearchParams(window.location.search).get('personId') || '';

  // ── DOM refs ────────────────────────────────────────────────────────────────
  var pageSub        = document.getElementById('page-sub');
  var startBtn       = document.getElementById('start-liveness-btn');
  var runtimeWarning = document.getElementById('runtime-warning');
  var statusText     = document.getElementById('status-text');
  var container      = document.getElementById('liveness-container');
  var resultBox      = document.getElementById('result-box');
  var loadingOverlay = document.getElementById('loading-overlay');
  var loadingLabel   = document.getElementById('loading-label');

  var currentSessionId = null;

  // ── Block accidental form navigation ───────────────────────────────────────
  document.addEventListener('submit', function (e) {
    e.preventDefault();
    // debug removed
  }, true);

  // ── Global error catchers ──────────────────────────────────────────────────
  window.addEventListener('error', function (e) {
    // debug removed
  });
  window.addEventListener('unhandledrejection', function (e) {
    // debug removed
  });

  // ── Page init ──────────────────────────────────────────────────────────────
  if (pageSub) {
    pageSub.textContent = personId
      ? 'Person ID: ' + personId + '. Click Start to run liveness and automatic verification.'
      : 'Missing person ID. Return to enrollment and try again.';
  }
  if (!personId && startBtn) {
    startBtn.disabled    = true;
    startBtn.textContent = 'No Person ID — go back and enroll first';
  }

  function showRuntimeWarning(message) {
    if (!runtimeWarning) return;
    runtimeWarning.textContent = message;
    runtimeWarning.classList.add('active');
  }

  function hideRuntimeWarning() {
    if (!runtimeWarning) return;
    runtimeWarning.textContent = '';
    runtimeWarning.classList.remove('active');
  }

  // ...removed debug helpers...

  function safeStringify(val) {
    if (val === null || val === undefined) return String(val);
    if (typeof val === 'string') return val;
    if (typeof val === 'number' || typeof val === 'boolean') return String(val);
    try { return JSON.stringify(val, null, 2); }
    catch (_) { return Object.prototype.toString.call(val); }
  }

  // ── Extract meaningful message from ANY error shape ────────────────────────
  // Azure SDK throws structured objects like:
  // { livenessError: 'EnvironmentNotSupported', recognitionError: 'NotRecognized' }
  // OR plain Error objects OR strings
  function extractErrorMessage(err) {
    if (!err) return 'unknown error';
    if (typeof err === 'string') return err;

    // Plain Error object
    if (err instanceof Error) return err.message || 'Error (no message)';

    // Azure SDK liveness error shape — SAFE: check typeof first before accessing properties
    if (typeof err === 'object' && err !== null) {
      var parts = [];
      if (typeof err.livenessError !== 'undefined' && err.livenessError !== null) {
        parts.push('livenessError: ' + String(err.livenessError));
      }
      if (typeof err.recognitionError !== 'undefined' && err.recognitionError !== null) {
        parts.push('recognitionError: ' + String(err.recognitionError));
      }
      if (parts.length > 0) return parts.join(' | ');

      // Object with message field
      if (typeof err.message !== 'undefined' && err.message !== null) {
        return String(err.message);
      }
    }

    // Fallback: stringify the whole thing
    return safeStringify(err);
  }

  // Removed copyDebugBtn block (undefined)

  // ── Status ─────────────────────────────────────────────────────────────────
  function setStatus(message) {
    if (statusText) statusText.textContent = message;
    // debug removed
  }

  function showLoading(msg) {
    if (loadingLabel)   loadingLabel.textContent = msg || 'LOADING...';
    if (loadingOverlay) loadingOverlay.classList.add('active');
  }

  function hideLoading() {
    if (loadingOverlay) loadingOverlay.classList.remove('active');
  }

  // ── Result box ─────────────────────────────────────────────────────────────
  function showResult(type, lines) {
    if (!resultBox) return;
    var colors = {
      pass:         '#107c10',
      fail:         '#b71c1c',
      inconclusive: '#a15c00',
    };
    var color = colors[type] || '#444';
    resultBox.style.display = 'block';
    resultBox.innerHTML =
      '<div style="font-weight:700;color:' + color + ';margin-bottom:10px;font-size:16px;">' +
        (type === 'pass' ? '✅ Verified' : type === 'fail' ? '❌ Failed' : '⚠️ Inconclusive') +
      '</div>' +
      lines.filter(Boolean).map(function (line) {
        return '<div style="font-size:13px;line-height:1.9;color:#444;padding:2px 0;">' + line + '</div>';
      }).join('');
  }

  // ── Response helpers ───────────────────────────────────────────────────────
  function parseResponseText(text) {
    try   { return text ? JSON.parse(text) : {}; }
    catch (_) { return { detail: text || '' };   }
  }

  function normalizeError(data, fallback) {
    if (!data || !data.detail) return fallback;
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) {
      return data.detail.map(function (e) {
        var loc = Array.isArray(e.loc) ? e.loc.join('.') + ': ' : '';
        return loc + (e.msg || JSON.stringify(e));
      }).join(' | ');
    }
    return JSON.stringify(data.detail);
  }

  function getTokenLivenessMode(authToken) {
    try {
      if (!authToken || authToken.indexOf('.') === -1) return 'unknown';
      var payloadPart = authToken.split('.')[1] || '';
      var padded = payloadPart.replace(/-/g, '+').replace(/_/g, '/');
      while (padded.length % 4 !== 0) padded += '=';
      var payloadJson = atob(padded);
      var payload = JSON.parse(payloadJson);
      var faceJson = payload && payload.face ? payload.face : null;
      if (!faceJson) return 'unknown';
      var face = typeof faceJson === 'string' ? JSON.parse(faceJson) : faceJson;
      var claims = face && face.clientClaims ? face.clientClaims : null;
      var mode = claims && claims.livenessOperationMode ? String(claims.livenessOperationMode) : 'unknown';
      return mode;
    } catch (_) {
      return 'unknown';
    }
  }

  // ── Camera permission preflight ────────────────────────────────────────────
  async function ensureCameraPermission() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error('Browser does not support camera access (getUserMedia).');
    }
    var stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      // debug removed
    } catch (e) {
      throw new Error(
        'Camera permission denied or unavailable. ' +
        'Click the camera icon in the address bar, allow access, then retry. ' +
        'Details: ' + e.message
      );
    } finally {
      if (stream) stream.getTracks().forEach(function (t) { t.stop(); });
    }
  }

  function ensureRuntimeSupport() {
    if (!window.isSecureContext) {
      throw new Error(
        'Liveness requires a secure context. Open the app via https:// or http://localhost.'
      );
    }

    var ua = (navigator.userAgent || '').toLowerCase();
    var isEmbedded = ua.indexOf('electron') !== -1 || ua.indexOf('wv') !== -1;
    if (isEmbedded) {
      throw new Error(
        'Embedded browser/webview detected. Run liveness in Chrome or Edge desktop browser.'
      );
    }
  }

  function applyRuntimeCompatibilityState() {
    try {
      ensureRuntimeSupport();
      hideRuntimeWarning();
    } catch (e) {
      var message = extractErrorMessage(e);
      showRuntimeWarning(message);
      if (startBtn) startBtn.disabled = true;
      setStatus('Runtime not supported for liveness.');
      // debug removed
    }
  }

  // ── Load SDK ───────────────────────────────────────────────────────────────
  async function loadSdk() {
    setStatus('Loading liveness SDK...');
    try {
      // debug removed
      var sdk = await import('/legacy/azure-ai-vision-face-ui/FaceLivenessDetector.js');
      // debug removed
      if (!sdk || typeof sdk !== 'object') {
        throw new Error('SDK import returned invalid module: ' + typeof sdk);
      }
      return sdk;
    } catch (e) {
      var msg = extractErrorMessage(e);
      // debug removed
      // debug removed
      try {
        var sdk2 = await import('/facelivenessdetector-assets/FaceLivenessDetector.js');
        // debug removed
        return sdk2;
      } catch (e2) {
        // debug removed
        throw new Error(
          'Could not load liveness SDK from either location. ' +
          'Primary: /legacy/azure-ai-vision-face-ui/FaceLivenessDetector.js. ' +
          'Fallback: /facelivenessdetector-assets/FaceLivenessDetector.js. ' +
          'Details: ' + msg
        );
      }
    }
  }

  // ── Create detector ────────────────────────────────────────────────────────
  function createDetector(tokenMode) {
    var existing = document.getElementById('face-liveness-detector');
    if (existing && existing.parentNode) {
      existing.parentNode.removeChild(existing);
    }

    var detector = document.createElement('azure-ai-vision-face-ui');
    detector.id = 'face-liveness-detector';

    // Work around a known legacy SDK transition issue where clicking Continue
    // on the tips screen can render a blank stage in some browsers.
    detector.skipInstructions = true;

    // This SDK build reads the private _baseAssetsURL field (not baseAssetsURL).
    // Use the legacy mount as canonical source for wasm/runtime assets.
    detector._baseAssetsURL = '/legacy/azure-ai-vision-face-ui';

    // This SDK requires active-mode=true to trigger active prompts even when
    // the token operation mode is PassiveActive.
    var normalizedMode = String(tokenMode || '').toLowerCase();
    if (normalizedMode === 'passiveactive') {
      detector.setAttribute('active-mode', 'true');
      // debug removed
    } else {
      detector.removeAttribute('active-mode');
      // debug removed
    }

    detector.style.display   = 'block';
    detector.style.width     = '100%';
    detector.style.minHeight = '420px';

    if (container) {
      container.style.display = 'block';
      container.appendChild(detector);
      // debug removed
      
      // SAFETY CHECK: verify detector is in DOM
      var containerStyle = window.getComputedStyle(container);
      // debug removed
    } else {
      document.body.appendChild(detector);
      // debug removed
    }

    // SAFETY CHECK after append
    var isInDOM = !!detector.offsetParent;
    var detectorComputed = window.getComputedStyle(detector);
    // debug removed

    return detector;
  }

  async function waitForDetectorRuntime(detector, timeoutMs) {
    var startedAt = Date.now();
    var timeout = typeof timeoutMs === 'number' ? timeoutMs : 20000;
    var probeCount = 0;

    // debug removed

    while ((Date.now() - startedAt) < timeout) {
      probeCount++;
      var shadowReady = !!(
        detector &&
        detector.shadowRoot &&
        detector.shadowRoot.getElementById('feedbackMessage')
      );
      var moduleReady = !!(
        window.Module &&
        window.Module.FaceRecoEngineAdapter
      );

      // debug removed

      if (shadowReady && moduleReady) {
        return;
      }

      await new Promise(function (r) { setTimeout(r, 250); });
    }

    var shadowReadyFinal = !!(
      detector &&
      detector.shadowRoot &&
      detector.shadowRoot.getElementById('feedbackMessage')
    );
    var moduleReadyFinal = !!(
      window.Module &&
      window.Module.FaceRecoEngineAdapter
    );

    throw new Error(
      'Detector runtime not ready in time (after ' + probeCount + ' probes). ' +
      'shadow=' + shadowReadyFinal + ', module=' + moduleReadyFinal +
      '. Likely cause: SDK assets not loading from /legacy/azure-ai-vision-face-ui/'
    );
  }

  async function autoStartDetectorChallenge(detector, timeoutMs) {
    var startedAt = Date.now();
    var timeout = typeof timeoutMs === 'number' ? timeoutMs : 12000;
    var clicked = false;
    var probeCount = 0;

    // debug removed

    while ((Date.now() - startedAt) < timeout) {
      try {
        probeCount++;
        var root = detector && detector.shadowRoot;
        
        if (!root) {
          await new Promise(function (r) { setTimeout(r, 120); });
          continue;
        }

        // shadowRoot exists! Let's inspect what's in it
        // debug removed

        var startBtn = root.getElementById ? root.getElementById('start') : null;
        var brightness = root.getElementById ? root.getElementById('brightness') : null;
        var camera = root.getElementById ? root.getElementById('camera') : null;
        var instructPanel = root.getElementById ? root.getElementById('instructions-panel') : null;
        var feedbackMsg = root.getElementById ? root.getElementById('feedbackMessage') : null;

        // debug removed

        if (startBtn && typeof startBtn.click === 'function') {
          startBtn.click();
          clicked = true;
          await new Promise(function (r) { setTimeout(r, 350); });
        }

        // Once camera stage is visible, we can stop trying.
        if (camera && camera.hidden === false) {
          return true;
        }

        if (brightness && brightness.hidden === true && clicked) {
          return true;
        }
      } catch (e) {
        // debug removed
      }

      await new Promise(function (r) { setTimeout(r, 120); });
    }

    // debug removed
    return false;
  }

  // ── Inspect SDK result ─────────────────────────────────────────────────────
  // Log everything about the SDK result so we know exactly what it returned
  function inspectSdkResult(result) {
    // debug removed
  }

  // ── Session creation (liveness + verify combined) ─────────────────────────
  // Creates a liveness+verify session. Azure receives the enrolled reference
  // image at creation time and runs similarity internally using the
  // liveness-guaranteed capture frame — no separate selfie step needed.
  async function createLivenessVerifySession() {
    if (!personId) throw new Error('No person ID available. Please enroll first.');
    setStatus('Creating liveness+verify session...');
    var lastErr = null;

    for (var attempt = 1; attempt <= 4; attempt++) {
      try {
        var res  = await fetch(API + '/api/liveness/verify-session-for-person', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            person_id:            personId,
            livenessOperationMode: 'PassiveActive',
          }),
        });
        var text = await res.text();
        var data = parseResponseText(text);

        if (res.ok && data.sessionId && data.authToken) {
          // debug removed
          return data;
        }

        lastErr = new Error(normalizeError(data, 'Failed to create liveness+verify session.'));
        // debug removed
      } catch (e) {
        lastErr = e;
        // debug removed
      }

      if (attempt < 4) {
        await new Promise(function (r) { setTimeout(r, 700 * attempt); });
      }
    }

    throw lastErr || new Error('Failed to create liveness+verify session after 4 attempts.');
  }

  // ── Fetch combined liveness+verify result ─────────────────────────────────
  // Polls GET /api/liveness/verify-session/{id}/result which returns both
  // the liveness decision and the similarity score in one response.
  async function fetchVerifySessionResult(sessionId) {
    if (!sessionId) throw new Error('Session ID is missing.');
    setStatus('Liveness challenge complete. Fetching result...');
    showLoading('PROCESSING RESULT...');

    var res = await fetch(
      API + '/api/liveness/verify-session/' + encodeURIComponent(sessionId) + '/result'
    );
    hideLoading();
    var text = await res.text();
    var data = parseResponseText(text);
    // debug removed
    if (!res.ok) throw new Error(normalizeError(data, 'Failed to fetch session result.'));
    return data;
  }

  // ── Cleanup ────────────────────────────────────────────────────────────────
  function cleanupDetector() {
    var detector = document.getElementById('face-liveness-detector');
    if (detector && detector.parentNode) {
      detector.parentNode.removeChild(detector);
      // debug removed
    }
    if (container) container.style.display = 'none';
  }

  // ── MAIN LIVENESS FLOW ─────────────────────────────────────────────────────
  async function startLivenessFlow() {
    if (!startBtn) return;
    startBtn.disabled = true;
    if (resultBox) resultBox.style.display = 'none';
    // debug removed

    try {

      // STEP 0 — Runtime checks (secure context + non-embedded browser)
      ensureRuntimeSupport();

      // STEP 1 — Load SDK
      var sdk = await loadSdk();

      // STEP 2 — Wait for custom element definition
      // debug removed
      await Promise.race([
        customElements.whenDefined('azure-ai-vision-face-ui'),
        new Promise(function (_, reject) {
          setTimeout(function () {
            reject(new Error(
              'Custom element not defined after 15s. ' +
              'Check that FaceLivenessDetector.js loaded correctly.'
            ));
          }, 15000);
        }),
      ]);
      // debug removed

      // STEP 3 — Camera permission preflight
      await ensureCameraPermission();

      // STEP 4 — Create Azure liveness+verify session
      // The enrolled reference image is sent to Azure at session creation.
      // Azure will run liveness challenge + similarity in one go.
      var session = await createLivenessVerifySession();
      currentSessionId = session.sessionId;
      var tokenMode = getTokenLivenessMode(session.authToken);
      // debug removed
      if (tokenMode.toLowerCase() !== 'passiveactive') {
        throw new Error(
          'Liveness session is not PassiveActive. Active challenges (smile/turn) cannot run. ' +
          'Please verify server mode configuration.'
        );
      }

      showResult('inconclusive', [
        '⏳ Liveness session created.',
        'Session ID: ' + currentSessionId,
        'Complete the on-screen challenge: center your face, smile, and follow the green indicator.',
      ]);

      // STEP 5 — Create detector element and show challenge UI
      var detector = createDetector(tokenMode);

      // STEP 5b — Ensure detector internals are ready before start()
      await waitForDetectorRuntime(detector, 22000);

      setStatus('Complete the liveness challenge — smile and follow the green indicator...');
      // debug removed

      // STEP 6 — Run SDK challenge
      // PassiveActive will ask user to: center face, smile, turn toward green
      // Give 120 seconds to complete the challenge
      
      // Safety check: ensure detector and detector.start exist
      if (!detector || typeof detector.start !== 'function') {
        throw new Error(
          'Detector element not initialized properly. ' +
          'detector type=' + typeof detector + ', start type=' + (detector && typeof detector.start) + '. ' +
          'Make sure FaceLivenessDetector.js custom element is defined.'
        );
      }
      if (!session || !session.authToken) {
        throw new Error(
          'Session or auth token is invalid. ' +
          'session=' + (session ? 'ok' : 'null') + ', token=' + (session && session.authToken ? 'present' : 'missing')
        );
      }

      var sdkResult;
      try {
        // debug removed
        if (detector) {
          // debug removed
          var computedStyle = window.getComputedStyle(detector);
          // debug removed
        }
        
        // debug removed
        var startPromise = detector.start(session.authToken);
        // debug removed
        
        sdkResult = await Promise.race([
          startPromise,
          new Promise(function (_, reject) {
            setTimeout(function () {
              reject(new Error('Challenge timed out after 120 seconds. Please retry.'));
            }, 120000);
          }),
        ]);
        // debug removed
      } catch (sdkErr) {
        // SDK threw an error — could be structured object or plain Error
        // debug removed
        var sdkErrMsg = extractErrorMessage(sdkErr);
        // debug removed

        // Check if it is the known "setActiveMode" issue
        if (sdkErrMsg.indexOf('setActiveMode') !== -1) {
          throw new Error(
            'SDK version incompatibility: setActiveMode is not supported. ' +
            'Please update the FaceLivenessDetector.js SDK files.'
          );
        }
        throw new Error('Liveness challenge error: ' + sdkErrMsg);
      }

      // STEP 7 — Inspect and log everything about the result
      inspectSdkResult(sdkResult);

      // Extract liveness status
      var LivenessStatus = (sdk && sdk.LivenessStatus) ? sdk.LivenessStatus : {};
      // debug removed

      var livenessStatus = String(
        (sdkResult && sdkResult.livenessStatus) ? sdkResult.livenessStatus : ''
      );
      var realFaceValue  = String(LivenessStatus.RealFace  || 'RealFace');
      var spoofFaceValue = String(LivenessStatus.SpoofFace || 'SpoofFace');
      var sdkMarkedSpoof = false;

      // debug removed

      // Spoof detected — stop immediately
      if (livenessStatus === spoofFaceValue) {
        sdkMarkedSpoof = true;
        // debug removed
      }

      // If SDK did not return RealFace, log it but still try server-side check
      if (livenessStatus && livenessStatus !== realFaceValue) {
        // debug removed
      }

      // STEP 8 — Fetch combined liveness + similarity result from server
      // debug removed
      var result = await fetchVerifySessionResult(currentSessionId);

      // STEP 9 — Display result
      var matchStatus  = String(result.matchStatus  || 'inconclusive').toLowerCase();
      var similarity   = typeof result.similarityPercent === 'number'
        ? result.similarityPercent.toFixed(2) + '%'
        : '-';
      var livDecision  = String(result.decision || '').toLowerCase();
      if (!livDecision || livDecision === 'unknown') {
        if (livenessStatus === realFaceValue) livDecision = 'pass';
        else if (livenessStatus === spoofFaceValue) livDecision = 'fail';
        else livDecision = 'unknown';
      }
      var livConf    = typeof result.livenessConfidence === 'number'
        ? result.livenessConfidence.toFixed(4)
        : '-';
      var livReason  = result.reason ? String(result.reason) : '';
      var thresholds = result.thresholds || {};

      var resultLines = [
        'Person ID: '    + personId,
        'Liveness: '     + livDecision + ' (confidence: ' + livConf + ')',
        livReason ? 'Liveness reason: ' + livReason : '',
        sdkMarkedSpoof ? 'SDK status: ' + livenessStatus + ' (spoof)' : (livenessStatus ? 'SDK status: ' + livenessStatus : ''),
        'Verification source: liveness_session_image',
        'Match status: ' + matchStatus,
        'Similarity: '   + similarity,
        thresholds.pass ? 'Pass threshold: ' + thresholds.pass + '%' : '',
      ];

      showResult(matchStatus, resultLines);
      setStatus('Completed.');
      // debug removed

    } catch (err) {
      hideLoading();
      var errMsg = extractErrorMessage(err);
      // debug removed
      setStatus('Failed.');
      showResult('fail', [
        'Liveness flow failed.',
        'Details: ' + errMsg,
        'Check the Technical diagnostics below for more information.',
      ]);

    } finally {
      startBtn.disabled = false;
      cleanupDetector();
      // debug removed
    }
  }

  // ── Attach button listener ─────────────────────────────────────────────────
  applyRuntimeCompatibilityState();
  setStatus('Ready.');


  // Custom: Only show liveness after user clicks Continue
  var continueBtn = document.getElementById('continue-btn');
  var instructionsPanel = document.getElementById('instructions-panel');
  if (continueBtn && startBtn) {
    continueBtn.addEventListener('click', function () {
      // Hide instructions, show start button
      if (instructionsPanel) instructionsPanel.style.display = 'none';
      startBtn.style.display = 'inline-block';
    });
    // debug removed
  }
  if (startBtn) {
    startBtn.addEventListener('click', function () {
      // debug removed
      startBtn.style.display = 'none';
      startLivenessFlow();
    });
    // debug removed
  } else {
    // debug removed
  }

}());