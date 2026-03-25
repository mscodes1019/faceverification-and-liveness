var API = window.location.origin;

var state = {
  userInfo:        { username: '', userId: '', personId: '' },
  newEnrollment:   { newPersonId: '' },
  loginNextScreen: 'enroll',
  consentGiven:    false,
  retentionEnabled:false,
  colorFrame:      null,
  stream:          null,
  qualityTimer:    null,
  captureCount:    0,
  goodFrameCount:  0,
  capturing:       false,
  faceApiChecked:  false,
  faceApiReady:    false,
  blockedReason:   '',
  faceCheckBusy:   false,
  lastFaceCheckAt: 0,
  lastFaceDetectedAt: 0,
};

var FRAMES_NEEDED = 2;
var verifyLiveStream = null;
var verifyLiveTimer = null;
var activeLivenessDetector = null;

// ── Image fallback ────────────────────────────────────────────────────────────
function imgFallback(el, emoji) {
  el.outerHTML = '<span style="font-size:24px;">' + emoji + '</span>';
}

// ── Navigation ────────────────────────────────────────────────────────────────
function goTo(screenId, param) {
// ...removed debug panel logic...
  document.querySelectorAll('.screen').forEach(function(s) {
    s.classList.remove('active');
  });
  document.getElementById(screenId).classList.add('active');
  if (screenId === 'screen-consent') {
    syncConsentChoices();
  }
  if (screenId === 'screen-login' && param) {
    state.loginNextScreen = param;
    setupLoginScreen(param);
  }
  if (screenId === 'screen-capture') {
    initializeSource();
  }
  if (screenId === 'screen-liveness') {
    hideLoading();
    closeVerifyLiveModal();
    setVerifyResult('inconclusive', [
      'Ready for liveness check.',
      'Click "Start liveness check" to continue.',
    ]);
  }
  window.scrollTo(0, 0);
}

// ── Login screen ──────────────────────────────────────────────────────────────
function setupLoginScreen(mode) {
  if (mode === 'manage') {
    document.getElementById('login-step-label').textContent = '';
    document.getElementById('login-headline').textContent   = 'Manage your profile';
    document.getElementById('login-sub').textContent        = 'Enter your name to continue. User ID will be assigned automatically.';
    document.getElementById('login-nav-title').textContent  = 'Manage profile';
  } else {
    document.getElementById('login-step-label').textContent = 'Step 1 of 3';
    document.getElementById('login-headline').textContent   = 'Tell us who you are';
    document.getElementById('login-sub').textContent        = 'Enter your full name to get started. A User ID will be generated automatically.';
    document.getElementById('login-nav-title').textContent  = 'Your details';
  }
}

function syncConsentChoices() {
  var consentEl = document.getElementById('consent-checkbox');
  var retainEl  = document.getElementById('retention-checkbox');
  var continueBtn = document.getElementById('consent-continue-btn');

  state.consentGiven = !!(consentEl && consentEl.checked);
  state.retentionEnabled = !!(retainEl && retainEl.checked);

  if (continueBtn) continueBtn.disabled = !state.consentGiven;
}

function proceedFromConsent() {
  syncConsentChoices();
  if (!state.consentGiven) {
    showErrorModal('Consent is required to proceed with enrollment.');
    return;
  }
  goTo('screen-login', 'enroll');
}

function validateLogin() {
  var name   = document.getElementById('input-name').value.trim();
  document.getElementById('login-next-btn').disabled = !name;
}

function loginBack() {
  if (state.loginNextScreen === 'manage') goTo('screen-welcome');
  else goTo('screen-consent');
}

function loginNext() {
  var userIdEl = document.getElementById('input-userid');
  state.userInfo.userId   = userIdEl ? userIdEl.value.trim() : '';
  state.userInfo.username = document.getElementById('input-name').value.trim();
  if (state.loginNextScreen === 'manage') goTo('screen-welcome');
  else goTo('screen-instruction');
}

// ── Modals ────────────────────────────────────────────────────────────────────
function showDeclineModal() { document.getElementById('modal-decline').classList.add('active'); }
function showLeaveModal()   { document.getElementById('modal-leave').classList.add('active'); }
function closeModal(id)     { document.getElementById(id).classList.remove('active'); }

function cancelEnrollment() {
  removeSource();
  resetAll();
  goTo('screen-welcome');
}

function retryCapture() {
  state.capturing      = false;
  state.goodFrameCount = 0;
  state.colorFrame     = null;
  goTo('screen-capture');
}

// ── Camera: start ─────────────────────────────────────────────────────────────
async function initializeSource() {
  state.capturing      = false;
  state.goodFrameCount = 0;
  state.colorFrame     = null;
  state.faceApiChecked = false;
  state.faceApiReady   = false;
  state.blockedReason  = '';
  state.faceCheckBusy  = false;
  state.lastFaceCheckAt = 0;
  state.lastFaceDetectedAt = 0;

  var oval = document.getElementById('oval-wrapper');
  if (oval) oval.classList.remove('ready');

  var countdown = document.getElementById('countdown-number');
  if (countdown) countdown.style.display = 'none';

  var statusTop = document.getElementById('capture-status');
  var statusBot = document.getElementById('status-below');
  if (statusTop) statusTop.textContent = 'POSITION YOUR FACE IN THE OVAL';
  if (statusBot) {
    statusBot.textContent = 'Center your face inside the oval';
    statusBot.className   = 'status-below';
  }

  try {
    state.stream = await navigator.mediaDevices.getUserMedia({
      video: {
        width:      { ideal: 1280 },
        height:     { ideal: 720 },
        facingMode: 'user',
      }
    });
    var video = document.getElementById('video');
    if (video) video.srcObject = state.stream;
    state.qualityTimer = setInterval(checkFrameQuality, 800);
  } catch (e) {
    if (statusTop) statusTop.textContent = 'CAMERA ACCESS DENIED';
    if (statusBot) statusBot.textContent = 'Please allow camera access and try again';
    console.error('Camera error:', e);
  }
}

// ── Camera: stop ──────────────────────────────────────────────────────────────
function removeSource() {
  if (state.stream) {
    state.stream.getTracks().forEach(function(t) { t.stop(); });
    state.stream = null;
  }
  if (state.qualityTimer) {
    clearInterval(state.qualityTimer);
    state.qualityTimer = null;
  }
  state.goodFrameCount = 0;
  state.capturing      = false;
  state.faceApiChecked = false;
  state.faceApiReady   = false;
  state.blockedReason  = '';
  state.faceCheckBusy  = false;
  state.lastFaceCheckAt = 0;
  state.lastFaceDetectedAt = 0;
}

async function checkFaceWithApi(video) {
  if (state.faceCheckBusy) return;

  var now = Date.now();
  if (now - state.lastFaceCheckAt < 1200) return;

  state.faceCheckBusy  = true;
  state.lastFaceCheckAt = now;

  try {
    var sample = document.createElement('canvas');
    sample.width  = 640;
    sample.height = 480;
    var sctx = sample.getContext('2d');
    sctx.drawImage(video, 0, 0, sample.width, sample.height);

    var res = await fetch(API + '/api/detect-face', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: sample.toDataURL('image/jpeg', 0.92) }),
    });

    // ...removed debug panel update logic...
    if (!res.ok) {
      var detail = '';
      try {
        var errData = await res.json();
        if (errData && errData.detail) detail = String(errData.detail);
      } catch (_) {
        detail = '';
      }

      state.faceApiChecked = true;
      // Preserve last successful face-ready signal to avoid flapping on transient API errors.
      state.faceApiReady = state.faceApiReady && (Date.now() - state.lastFaceDetectedAt) < 8000;
      state.blockedReason = detail || ('Face check temporarily unavailable (' + res.status + '). Keep face centered and retry.');
      // ...removed debug panel error display...
      return;
    }

    var data = await res.json();
    state.faceApiChecked = true;
    state.faceApiReady   = !!data.exactly_one_face;
    state.blockedReason  = data.blocked_reason || '';
    if (state.faceApiReady) state.lastFaceDetectedAt = Date.now();
    // ...removed debug panel HTML update...
  } catch (e) {
    state.faceApiChecked = true;
    state.faceApiReady   = false;
    state.blockedReason  = '';
  } finally {
    state.faceCheckBusy = false;
  }
}

async function confirmFaceBeforeCapture(video) {
  try {
    var sample = document.createElement('canvas');
    sample.width  = 320;
    sample.height = 240;
    var sctx = sample.getContext('2d');
    sctx.drawImage(video, 0, 0, sample.width, sample.height);

    var res = await fetch(API + '/api/detect-face', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: sample.toDataURL('image/jpeg', 0.8) }),
    });

    if (!res.ok) return false;

    var data = await res.json();
    return !!data.exactly_one_face;
  } catch (e) {
    return false;
  }
}

// ── Auto-capture quality check (runs every 800ms) ─────────────────────────────
async function checkFrameQuality() {
  if (state.capturing) return;

  var video = document.getElementById('video');
  if (!video || video.videoWidth === 0) return;

  checkFaceWithApi(video);

  // Sample a 64x64 canvas to measure brightness
  var sc    = document.createElement('canvas');
  sc.width  = 64;
  sc.height = 64;
  var ctx   = sc.getContext('2d');
  ctx.drawImage(video, 0, 0, 64, 64);
  var data  = ctx.getImageData(0, 0, 64, 64).data;

  var brightness = 0;
  for (var i = 0; i < data.length; i += 4) {
    brightness += (data[i] + data[i+1] + data[i+2]) / 3;
  }
  brightness /= (data.length / 4);

  var lightGood = brightness > 50 && brightness < 230;
  var recentFace = (Date.now() - state.lastFaceDetectedAt) < 6000;
  var faceGood  = state.faceApiChecked && (state.faceApiReady || recentFace);
  var allGood   = faceGood && lightGood;

  setQuality('q-face',  'q-face-label',  faceGood,  'FACE');
  setQuality('q-light', 'q-light-label', lightGood, 'LIGHT');
  setQuality('q-pos',   'q-pos-label',   allGood,   'POSITION');

  var oval      = document.getElementById('oval-wrapper');
  var countdown = document.getElementById('countdown-number');
  var statusTop = document.getElementById('capture-status');
  var statusBot = document.getElementById('status-below');

  if (allGood) {
    state.goodFrameCount++;
    var remaining = FRAMES_NEEDED - state.goodFrameCount;

    if (oval) oval.classList.add('ready');

    if (remaining > 0) {
      if (countdown) { countdown.style.display = 'block'; countdown.textContent = remaining; }
      if (statusTop) statusTop.textContent = 'HOLD STILL — ' + remaining + '...';
      if (statusBot) { statusBot.textContent = 'Hold still, capturing soon...'; statusBot.className = 'status-below good'; }
    } else {
      if (countdown) countdown.style.display = 'none';
      state.capturing = true;
      if (statusTop) statusTop.textContent = 'FINAL FACE CHECK...';
      if (statusBot) { statusBot.textContent = 'Verifying face before capture...'; statusBot.className = 'status-below good'; }

      var confirmed = await confirmFaceBeforeCapture(video);
      if (!confirmed) {
        state.capturing = false;
        state.goodFrameCount = 0;
        if (statusTop) statusTop.textContent = 'POSITION YOUR FACE IN THE OVAL';
        if (statusBot) {
          statusBot.textContent = 'Face not confirmed at capture time — try again';
          statusBot.className = 'status-below';
        }
        return;
      }

      if (statusTop) statusTop.textContent = '✓ CAPTURING...';
      if (statusBot) { statusBot.textContent = 'Got it!'; statusBot.className = 'status-below good'; }
      captureColorFrame();
    }

  } else {
    // Be tolerant to momentary detection jitter so capture can still complete.
    if (faceGood || lightGood) state.goodFrameCount = Math.max(0, state.goodFrameCount - 1);
    else state.goodFrameCount = 0;
    if (oval) oval.classList.remove('ready');
    if (countdown) countdown.style.display = 'none';
    if (statusTop) statusTop.textContent = 'POSITION YOUR FACE IN THE OVAL';
    if (statusBot) {
      if (!state.faceApiChecked) {
        statusBot.textContent = 'Checking for a face...';
      } else if (state.blockedReason) {
        statusBot.textContent = state.blockedReason;
      } else if (!faceGood) {
        statusBot.textContent = 'No single face detected — be alone and look at camera';
      } else {
        statusBot.textContent = 'Poor lighting — move to a brighter area';
      }
      statusBot.className = 'status-below';
    }
  }
}

function setQuality(dotId, labelId, good, label) {
  var dot = document.getElementById(dotId);
  var lbl = document.getElementById(labelId);
  if (!dot || !lbl) return;
  dot.className   = 'q-dot ' + (good ? 'good' : 'bad');
  lbl.textContent = label;
}

// ── Capture photo from video ──────────────────────────────────────────────────
function captureColorFrame() {
  var video  = document.getElementById('video');
  var canvas = document.getElementById('canvas');

  var srcW = video.videoWidth;
  var srcH = video.videoHeight;
  var maxSide = 960;
  var scale = Math.min(1, maxSide / Math.max(srcW, srcH));
  canvas.width  = Math.round(srcW * scale);
  canvas.height = Math.round(srcH * scale);

  // Un-mirror image for accurate Face API detection
  var ctx = canvas.getContext('2d');
  ctx.translate(canvas.width, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(video, 0, 0);

  // Slight compression keeps quality adequate while reducing request latency.
  state.colorFrame = canvas.toDataURL('image/jpeg', 0.9);
  state.captureCount++;

  // Mark progress dot
  var dot = document.getElementById('pdot-' + (state.captureCount - 1));
  if (dot) dot.classList.add('done');

  removeSource();
  enroll();
}

// ── Enroll: send to backend ───────────────────────────────────────────────────
async function enroll() {
  // Normalize payload values to avoid FastAPI/Pydantic type validation errors.
  var userId   = String(state.userInfo.userId || '').trim();
  var username = String(state.userInfo.username || '').trim();
  var imageB64 = (typeof state.colorFrame === 'string') ? state.colorFrame : '';

  if (!username) {
    showErrorModal('Missing user details. Please go back and enter your name.');
    return;
  }

  if (!imageB64) {
    showErrorModal('No image was captured. Please try again.');
    return;
  }

  showLoading('UPLOADING IMAGE...');

  var steps = [
    'UPLOADING IMAGE...',
    'DETECTING FACE...',
    'GENERATING FACE TEMPLATE...',
    'FINALISING...',
  ];
  var si       = 0;
  var interval = setInterval(function() {
    si = (si + 1) % steps.length;
    var lbl = document.getElementById('loading-label');
    if (lbl) lbl.textContent = steps[si];
  }, 1500);

  try {
    var res = await fetch(API + '/api/enroll', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        user_id:   userId || null,
        name:      username,
        consent:   state.consentGiven,
        retention: state.retentionEnabled,
        image_b64: imageB64,
      }),
    });

    clearInterval(interval);
    hideLoading();

    var data = await res.json();

    if (res.ok) {
      state.newEnrollment.newPersonId = data.person_id;
      state.userInfo.personId         = data.person_id;
      showReceipt(data);
      goTo('screen-receipt');
    } else {
      // Handle all FastAPI error shapes
      var errMsg;
      if (!data.detail) {
        errMsg = 'Enrollment failed. Please try again.';
      } else if (typeof data.detail === 'string') {
        // Plain string error e.g. "No face detected"
        errMsg = data.detail;
      } else if (Array.isArray(data.detail)) {
        // FastAPI validation error — array of {msg, loc, type}
        errMsg = data.detail.map(function(e) {
          var loc = Array.isArray(e.loc) ? e.loc.join('.') + ': ' : '';
          return loc + (e.msg || JSON.stringify(e));
        }).join('\n');
      } else if (typeof data.detail === 'object') {
        // Some other object
        errMsg = data.detail.message || JSON.stringify(data.detail);
      } else {
        errMsg = String(data.detail);
      }
      showErrorModal(errMsg);
    }

  } catch (e) {
    clearInterval(interval);
    hideLoading();
    showErrorModal('Network error: ' + e.message);
    console.error('Enroll fetch error:', e);
  }
}

// ── Error modal ───────────────────────────────────────────────────────────────
function showErrorModal(msg) {
  var el = document.getElementById('modal-error-msg');
  if (el) el.textContent = msg;
  document.getElementById('modal-error').classList.add('active');
  console.error('Enrollment error:', msg);
}

// ── Receipt screen ────────────────────────────────────────────────────────────
function showReceipt(data) {
  var el = document.getElementById('receipt-details');
  if (!el) return;

  var vr = document.getElementById('verify-result');
  if (vr) {
    vr.style.display = 'none';
    vr.innerHTML = '';
  }

  el.innerHTML =
    '<div><span style="color:#999;">User ID</span> &nbsp; '    + (data.user_id   || '-') + '</div>' +
    '<div><span style="color:#999;">Name</span> &nbsp; '       + (data.name      || '-') + '</div>' +
    '<div><span style="color:#999;">Person ID</span> &nbsp; '  + (data.person_id || '-') + '</div>' +
    '<div><span style="color:#999;">Model</span> &nbsp; '      + (data.model     || '-') + '</div>' +
    '<div><span style="color:#999;">Enrolled at</span> &nbsp; '
      + (data.enrolled_at ? new Date(data.enrolled_at).toLocaleString() : '-') + '</div>';
}

function goToLivenessPage() {
  var personId = String(state.userInfo.personId || state.newEnrollment.newPersonId || '').trim();
  if (!personId) {
    showErrorModal('No Person ID available. Enroll first, then run liveness check.');
    return;
  }

  window.location.href = '/liveness-check.html?personId=' + encodeURIComponent(personId);
}

function setVerifyResult(type, lines) {
  var vr = document.getElementById('verify-result');
  if (!vr) return;

  var color = '#444';
  if (type === 'pass') color = '#107c10';
  else if (type === 'fail') color = '#b71c1c';
  else if (type === 'inconclusive') color = '#a15c00';

  vr.style.display = 'block';
  vr.innerHTML =
    '<div style="font-weight:700; color:' + color + '; margin-bottom:6px;">Verification result</div>' +
    lines.map(function(line) {
      return '<div style="font-size:12px; line-height:1.7; color:#555;">' + line + '</div>';
    }).join('');
}

async function verifyWithProbe(source, imageB64) {
  var personId = String(state.userInfo.personId || state.newEnrollment.newPersonId || '').trim();
  if (!personId) {
    setVerifyResult('fail', ['No Person ID available. Enroll first, then verify.']);
    return;
  }

  // Run liveness first. Similarity verify continues only on liveness pass.
  var liveness = await runLivenessGate();
  if (!liveness.ok) {
    var livenessReason = String(liveness.reason || 'liveness_below_threshold');
    var lowerReason = livenessReason.toLowerCase();
    var unsupportedEnv = lowerReason.indexOf('environmentnotsupported') !== -1;
    var sdkClientError = lowerReason.indexOf('liveness_client_error:') !== -1;
    var permissionDenied =
      lowerReason.indexOf('notallowederror') !== -1 ||
      lowerReason.indexOf('permission') !== -1 ||
      lowerReason.indexOf('securityerror') !== -1;
    var appUrl = (window && window.location && window.location.origin)
      ? (window.location.origin + '/enroll.html')
      : 'http://127.0.0.1:8050/enroll.html';

    if (permissionDenied) {
      setVerifyResult('fail', [
        'Liveness failed: camera permission was denied or blocked.',
        'In Chrome, click the camera icon in the address bar and allow camera access.',
        'Then reload this exact app URL and retry: ' + appUrl,
        'Details: ' + livenessReason,
      ]);
      return;
    }

    if (unsupportedEnv) {
      setVerifyResult('fail', [
        'Liveness SDK reported this environment as unsupported.',
        'Use latest Microsoft Edge or Google Chrome on your local machine (not remote desktop/webview).',
        'Open this app URL directly and retry: ' + appUrl,
        'Details: ' + livenessReason,
      ]);
      return;
    }

    if (sdkClientError) {
      var sdkBootstrapError =
        lowerReason.indexOf('liveness_sdk_import_timeout') !== -1 ||
        lowerReason.indexOf('liveness_custom_element_define_timeout') !== -1 ||
        lowerReason.indexOf('legacy_detector_not_ready') !== -1;

      if (sdkBootstrapError) {
        setVerifyResult('fail', [
          'Liveness UI failed to load in this browser session.',
          'Hard refresh this page (Ctrl+F5) and retry.',
          'If it persists, use latest Edge/Chrome and ensure WebAssembly is enabled.',
          'Details: ' + livenessReason,
        ]);
        return;
      }

      setVerifyResult('fail', [
        'Liveness SDK failed to initialize in this browser runtime.',
        'Verification is blocked until liveness can run successfully.',
        'Details: ' + livenessReason,
      ]);
      return;
    }

    setVerifyResult('fail', [
      'Liveness failed.',
      'Decision: ' + String(liveness.decision || 'fail'),
      'Reason: ' + livenessReason,
      'Confidence: ' + (typeof liveness.livenessConfidence === 'number' ? liveness.livenessConfidence.toFixed(4) : '-'),
      'Threshold: ' + (typeof liveness.threshold === 'number' ? liveness.threshold.toFixed(2) : '-'),
    ]);
    return;
  }

  showLoading('VERIFYING...');
  try {
    var res;
    if (source === 'live') {
      res = await fetch(API + '/api/verify/live-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: liveness.sessionId,
          reference_person_id: personId,
        }),
      });
    } else {
      res = await fetch(API + '/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source: source,
          uploaded_image_b64: imageB64,
          reference_person_id: personId,
          liveness_session_id: liveness.sessionId || null,
        }),
      });
    }

    var text = await res.text();
    var data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { detail: text || '' };
    }
    hideLoading();

    if (!res.ok) {
      var msg;
      if (!data.detail) msg = 'Verification failed.';
      else if (typeof data.detail === 'string') msg = data.detail;
      else if (Array.isArray(data.detail)) {
        msg = data.detail.map(function(e) {
          var loc = Array.isArray(e.loc) ? e.loc.join('.') + ': ' : '';
          return loc + (e.msg || JSON.stringify(e));
        }).join(' | ');
      } else msg = JSON.stringify(data.detail);
      setVerifyResult('fail', [String(msg)]);
      return;
    }

    var backendLiveness = (data && data.liveness) ? data.liveness : null;
    var effectiveLiveness = backendLiveness || liveness;
    var status = String(data.matchStatus || 'inconclusive').toLowerCase();
    var similarity = (typeof data.similarityPercent === 'number')
      ? (data.similarityPercent.toFixed(2) + '%')
      : '-';

    var effectiveDecision = String((effectiveLiveness && effectiveLiveness.decision) || 'inconclusive').toLowerCase();
    var effectiveConfidence = (effectiveLiveness && typeof effectiveLiveness.livenessConfidence === 'number')
      ? effectiveLiveness.livenessConfidence
      : null;

    var lines = [
      'Source: ' + source,
      'Person ID: ' + personId,
      'Liveness: ' + effectiveDecision +
        ' (' + (effectiveConfidence !== null ? effectiveConfidence.toFixed(4) : '-') + ')',
      'Status: ' + status,
      'Similarity: ' + similarity,
    ];
    if (effectiveLiveness && effectiveLiveness.reason) {
      lines.push('Liveness reason: ' + String(effectiveLiveness.reason));
    }
    if (data.qualityReason) lines.push('Reason: ' + data.qualityReason);
    if (Array.isArray(data.qualityReasons) && data.qualityReasons.length > 0) {
      lines.push('Quality reasons: ' + data.qualityReasons.join(', '));
    }

    setVerifyResult(status, lines);
  } catch (e) {
    hideLoading();
    setVerifyResult('fail', ['Network error: ' + e.message]);
  }
}

async function verifyLiveFallbackWithoutLiveness(personId, livenessReason) {
  var imageB64 = await captureSingleLiveImageDataUrl();
  if (!imageB64) {
    setVerifyResult('inconclusive', [
      'Could not capture a live image for fallback verification.',
      'Details: ' + String(livenessReason || 'environment_not_supported'),
    ]);
    return;
  }

  showLoading('VERIFYING (FALLBACK)...');
  try {
    var res = await fetch(API + '/api/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: 'live',
        live_image_b64: imageB64,
        reference_person_id: personId,
      }),
    });

    var text = await res.text();
    var data;
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { detail: text || '' };
    }
    hideLoading();

    if (!res.ok) {
      var msg;
      if (!data.detail) msg = 'Fallback verification failed.';
      else if (typeof data.detail === 'string') msg = data.detail;
      else if (Array.isArray(data.detail)) {
        msg = data.detail.map(function(e) {
          var loc = Array.isArray(e.loc) ? e.loc.join('.') + ': ' : '';
          return loc + (e.msg || JSON.stringify(e));
        }).join(' | ');
      } else msg = JSON.stringify(data.detail);
      setVerifyResult('fail', [String(msg)]);
      return;
    }

    var status = String(data.matchStatus || 'inconclusive').toLowerCase();
    var similarity = (typeof data.similarityPercent === 'number')
      ? (data.similarityPercent.toFixed(2) + '%')
      : '-';

    var lines = [
      'Source: live (fallback)',
      'Person ID: ' + personId,
      'Liveness: skipped (SDK environment unsupported)',
      'Status: ' + status,
      'Similarity: ' + similarity,
      'Liveness details: ' + String(livenessReason || 'environment_not_supported'),
    ];
    if (data.qualityReason) lines.push('Reason: ' + data.qualityReason);
    if (Array.isArray(data.qualityReasons) && data.qualityReasons.length > 0) {
      lines.push('Quality reasons: ' + data.qualityReasons.join(', '));
    }

    setVerifyResult(status, lines);
  } catch (e) {
    hideLoading();
    setVerifyResult('fail', ['Network error during fallback verification: ' + e.message]);
  }
}

async function captureSingleLiveImageDataUrl() {
  var video = document.getElementById('verify-live-video');
  var msg = document.getElementById('verify-live-msg');
  var modal = document.getElementById('modal-verify-live');

  if (!video || !msg || !modal) {
    return null;
  }

  closeVerifyLiveModal();

  try {
    verifyLiveStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' }
    });
    video.srcObject = verifyLiveStream;
    modal.classList.add('active');
    await video.play();

    msg.textContent = 'Position your face in frame...';
    await new Promise(function(resolve) { setTimeout(resolve, 1200); });

    var canvas = document.createElement('canvas');
    var srcW = video.videoWidth || 1280;
    var srcH = video.videoHeight || 720;
    var maxSide = 960;
    var scale = Math.min(1, maxSide / Math.max(srcW, srcH));
    canvas.width = Math.round(srcW * scale);
    canvas.height = Math.round(srcH * scale);

    var ctx = canvas.getContext('2d');
    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    return canvas.toDataURL('image/jpeg', 0.9);
  } catch (_) {
    return null;
  } finally {
    closeVerifyLiveModal();
  }
}

async function runLivenessGate() {
  try {
    setVerifyResult('inconclusive', [
      'Creating liveness session...',
    ]);
    var createRes = await fetch(API + '/api/liveness/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        livenessOperationMode: 'PassiveActive',
      }),
    });

    var createData = await createRes.json().catch(function() { return {}; });
    if (!createRes.ok || !createData.sessionId) {
      setVerifyResult('fail', [
        'Could not create liveness session.',
        'Details: ' + String(createData.detail || 'liveness_session_create_failed'),
      ]);
      return {
        ok: false,
        decision: 'fail',
        reason: String(createData.detail || 'liveness_session_create_failed'),
      };
    }

    var sessionId = createData.sessionId;
    var authToken = String(createData.authToken || '');
    setVerifyResult('inconclusive', [
      'Liveness session created.',
      'Launching liveness challenge UI...',
    ]);
    var sdkResult = await runLegacyLivenessClient(authToken);

    if (!sdkResult || !sdkResult.ok) {
      setVerifyResult('fail', [
        'Liveness challenge did not complete.',
        'Reason: ' + String((sdkResult && sdkResult.reason) || 'liveness_client_failed'),
      ]);
      return {
        ok: false,
        sessionId: sessionId,
        decision: (sdkResult && sdkResult.decision) ? sdkResult.decision : 'inconclusive',
        reason: (sdkResult && sdkResult.reason) ? sdkResult.reason : 'liveness_client_failed',
        livenessConfidence: (sdkResult && typeof sdkResult.livenessConfidence === 'number') ? sdkResult.livenessConfidence : 0,
        threshold: 0.7,
      };
    }

    setVerifyResult('inconclusive', [
      'Liveness challenge completed.',
      'Validating liveness result with server...',
    ]);
    var serverLiveness = await pollLivenessResult(sessionId);

    if (!serverLiveness || String(serverLiveness.decision || '').toLowerCase() !== 'pass' || !serverLiveness.isLive) {
      return {
        ok: false,
        sessionId: sessionId,
        decision: String((serverLiveness && serverLiveness.decision) || 'inconclusive').toLowerCase(),
        reason: (serverLiveness && serverLiveness.reason) ? String(serverLiveness.reason) : 'liveness_not_passed',
        livenessConfidence: (serverLiveness && typeof serverLiveness.livenessConfidence === 'number')
          ? serverLiveness.livenessConfidence
          : 0,
        threshold: (serverLiveness && typeof serverLiveness.threshold === 'number')
          ? serverLiveness.threshold
          : 0.7,
        raw: serverLiveness || null,
      };
    }

    return {
      ok: true,
      sessionId: sessionId,
      decision: 'pass',
      reason: null,
      livenessConfidence: (typeof serverLiveness.livenessConfidence === 'number')
        ? serverLiveness.livenessConfidence
        : ((typeof sdkResult.livenessConfidence === 'number') ? sdkResult.livenessConfidence : 1.0),
      threshold: (typeof serverLiveness.threshold === 'number') ? serverLiveness.threshold : 0.7,
      raw: serverLiveness || sdkResult.raw || null,
    };
  } catch (e) {
    setVerifyResult('fail', [
      'Liveness request failed.',
      'Details: ' + e.message,
    ]);
    return {
      ok: false,
        decision: 'fail',
      reason: 'liveness_network_error: ' + e.message,
    };
  }
}

async function pollLivenessResult(sessionId) {
  var retries = 6;
  for (var i = 0; i < retries; i++) {
    try {
      var res = await fetch(API + '/api/liveness/session/' + encodeURIComponent(sessionId) + '/result');
      var data = await res.json().catch(function() { return {}; });
      if (!res.ok) {
        return {
          decision: 'fail',
          reason: String(data.detail || 'liveness_result_fetch_failed'),
          livenessConfidence: 0,
          threshold: 0.7,
          isLive: false,
        };
      }

      var decision = String(data.decision || '').toLowerCase();
      var sessionStatus = String(data.sessionStatus || '').toLowerCase();
      if (decision === 'pass' || decision === 'fail' || sessionStatus === 'resultavailable' || i === retries - 1) {
        return data;
      }
    } catch (_) {
      if (i === retries - 1) {
        return {
          decision: 'fail',
          reason: 'liveness_result_fetch_failed',
          livenessConfidence: 0,
          threshold: 0.7,
          isLive: false,
        };
      }
    }

    await new Promise(function(resolve) { setTimeout(resolve, 500); });
  }

  return {
    decision: 'inconclusive',
    reason: 'capture_not_completed',
    livenessConfidence: 0,
    threshold: 0.7,
    isLive: false,
  };
}

async function runLegacyLivenessClient(authToken) {
  if (!authToken) {
    return { ok: false, decision: 'fail', reason: 'missing_auth_token' };
  }

  try {
    showLivenessModal('Initializing liveness challenge...');

    var sdk = await Promise.race([
      import('/legacy/azure-ai-vision-face-ui/FaceLivenessDetector.js'),
      new Promise(function(_, reject) {
        setTimeout(function() { reject(new Error('liveness_sdk_import_timeout')); }, 15000);
      }),
    ]);
    var LivenessStatus = sdk.LivenessStatus || {};
    var RecognitionStatus = sdk.RecognitionStatus || {};

    var detector = document.createElement('azure-ai-vision-face-ui');
    activeLivenessDetector = detector;
    // Critical: point legacy web component runtime assets to mounted route.
    detector._baseAssetsURL = '/legacy/azure-ai-vision-face-ui';
    // Force challenge workflow prompts (smile/turn/look) for stricter liveness UX.
    detector.setAttribute('active-mode', 'true');
    // Avoid legacy SDK instruction-page blank transition on Continue.
    detector.skipInstructions = true;
    detector.style.display = 'block';
    detector.style.width = '100%';
    detector.style.minHeight = '420px';

    var host = document.getElementById('liveness-sdk-host');
    if (host) {
      host.innerHTML = '';
      host.appendChild(detector);
    } else {
      document.body.appendChild(detector);
    }

    if (!customElements.get('azure-ai-vision-face-ui')) {
      await Promise.race([
        customElements.whenDefined('azure-ai-vision-face-ui'),
        new Promise(function(_, reject) {
          setTimeout(function() { reject(new Error('liveness_custom_element_define_timeout')); }, 12000);
        }),
      ]);
    }

    await waitForLegacyDetectorReady(detector, 10000);
    showLivenessModal('Follow the on-screen liveness instructions...');

    try {
      var result = await Promise.race([
        detector.start(authToken),
        new Promise(function(_, reject) {
          setTimeout(function() { reject(new Error('liveness_start_timeout')); }, 30000);
        }),
      ]);
      var status = String(result && result.livenessStatus ? result.livenessStatus : '');

      if (status === String(LivenessStatus.RealFace || 'RealFace')) {
        return {
          ok: true,
          livenessConfidence: 1.0,
          raw: result,
        };
      }

      if (status === String(LivenessStatus.SpoofFace || 'SpoofFace')) {
        return {
          ok: false,
          decision: 'fail',
          reason: 'spoof_detected',
          livenessConfidence: 1.0,
          raw: result,
        };
      }

      if (status === String(LivenessStatus.ResultQueryableFromService || 'ResultQueryableFromService')) {
        return {
          ok: false,
          decision: 'inconclusive',
          reason: 'capture_not_completed',
          livenessConfidence: 0.0,
          raw: result,
        };
      }

      var verifyStatus = String(result && result.recognitionResult && result.recognitionResult.status
        ? result.recognitionResult.status
        : '');
      if (verifyStatus === String(RecognitionStatus.NotRecognized || 'NotRecognized')) {
        return {
          ok: false,
          decision: 'fail',
          reason: 'verification_not_recognized',
          livenessConfidence: 1.0,
          raw: result,
        };
      }

      return {
        ok: false,
        decision: 'inconclusive',
        reason: 'capture_not_completed',
        livenessConfidence: 0.0,
        raw: result,
      };
    } finally {
      if (detector && detector.parentNode) detector.parentNode.removeChild(detector);
      activeLivenessDetector = null;
      hideLivenessModal();
    }
  } catch (e) {
    hideLivenessModal();
    var msg = e && e.message ? String(e.message) : String(e || 'unknown_error');
    if (typeof e === 'object' && e && (e.livenessError || e.recognitionError)) {
      msg = 'liveness_' + String(e.livenessError || 'Unexpected') +
        (e.recognitionError ? ('_recognition_' + String(e.recognitionError)) : '');
    }
    return {
      ok: false,
      decision: 'fail',
      reason: 'liveness_client_error: ' + msg,
      livenessConfidence: 0.0,
    };
  }
}

function showLivenessModal(message) {
  var modal = document.getElementById('modal-verify-live');
  var msg = document.getElementById('verify-live-msg');
  var video = document.getElementById('verify-live-video');
  var host = document.getElementById('liveness-sdk-host');
  if (msg) msg.textContent = message || 'Starting liveness...';
  if (video) video.style.display = 'none';
  if (host) {
    host.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;min-height:320px;color:#fff;font-family:var(--mono);font-size:12px;letter-spacing:1px;text-align:center;padding:16px;">Loading liveness UI...</div>';
  }
  if (modal) modal.classList.add('active');
}

function hideLivenessModal() {
  var modal = document.getElementById('modal-verify-live');
  var host = document.getElementById('liveness-sdk-host');
  var video = document.getElementById('verify-live-video');
  if (host) host.innerHTML = '';
  if (video) video.style.display = 'block';
  if (modal) modal.classList.remove('active');
}

async function waitForLegacyDetectorReady(detector, timeoutMs) {
  var started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      var root = detector && detector.shadowRoot;
      var brightness = root && root.getElementById ? root.getElementById('brightness') : null;
      var parentContainer = root && root.getElementById ? root.getElementById('parentContainer') : null;
      var startBtn = root && root.getElementById ? root.getElementById('start') : null;
      if (brightness && parentContainer && startBtn) {
        return true;
      }
    } catch (_) {
      // Keep polling until timeout.
    }
    await new Promise(function(resolve) { setTimeout(resolve, 50); });
  }
  throw new Error('legacy_detector_not_ready');
}

async function verifyUsingCamera() {
  try {
    // Single-capture path: liveness component captures once, backend reuses that same image for similarity verify.
    await verifyWithProbe('live', null);
  } catch (e) {
    setVerifyResult('fail', ['Unable to start live verification: ' + e.message]);
  }
}

async function checkVerifyFaceWithApi(video) {
  try {
    var sample = document.createElement('canvas');
    sample.width = 640;
    sample.height = 480;
    var sctx = sample.getContext('2d');
    sctx.drawImage(video, 0, 0, sample.width, sample.height);

    var res = await fetch(API + '/api/detect-face', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_b64: sample.toDataURL('image/jpeg', 0.85) }),
    });

    if (!res.ok) {
      return { exactlyOne: false, blockedReason: '' };
    }

    var data = await res.json();
    return {
      exactlyOne: !!data.exactly_one_face,
      blockedReason: data.blocked_reason || '',
    };
  } catch (e) {
    return { exactlyOne: false, blockedReason: '' };
  }
}

function closeVerifyLiveModal() {
  var modal = document.getElementById('modal-verify-live');
  var video = document.getElementById('verify-live-video');
  var host = document.getElementById('liveness-sdk-host');
  if (verifyLiveTimer) {
    clearInterval(verifyLiveTimer);
    verifyLiveTimer = null;
  }
  if (verifyLiveStream) {
    verifyLiveStream.getTracks().forEach(function(t) { t.stop(); });
    verifyLiveStream = null;
  }
  if (activeLivenessDetector && activeLivenessDetector.parentNode) {
    activeLivenessDetector.parentNode.removeChild(activeLivenessDetector);
    activeLivenessDetector = null;
  }
  if (host) host.innerHTML = '';
  if (video) video.srcObject = null;
  if (video) video.style.display = 'block';
  if (modal) modal.classList.remove('active');
}

function fileToDataURL(file) {
  return new Promise(function(resolve, reject) {
    var reader = new FileReader();
    reader.onload = function() { resolve(String(reader.result || '')); };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function verifyUsingUpload(input) {
  try {
    var file = input && input.files && input.files[0];
    if (!file) return;
    var dataUrl = await fileToDataURL(file);
    await verifyWithProbe('upload', dataUrl);
  } catch (e) {
    setVerifyResult('fail', ['Unable to read selected file: ' + e.message]);
  } finally {
    if (input) input.value = '';
  }
}

// ── Loading overlay ───────────────────────────────────────────────────────────
function showLoading(msg) {
  var lbl = document.getElementById('loading-label');
  if (lbl) lbl.textContent = msg || 'LOADING...';
  document.getElementById('loading-overlay').classList.add('active');
}

function hideLoading() {
  document.getElementById('loading-overlay').classList.remove('active');
}

// ── Reset all state ───────────────────────────────────────────────────────────
function resetAll() {
  state.userInfo       = { username: '', userId: '', personId: '' };
  state.newEnrollment  = { newPersonId: '' };
  state.consentGiven   = false;
  state.retentionEnabled = false;
  state.colorFrame     = null;
  state.captureCount   = 0;
  state.goodFrameCount = 0;
  state.capturing      = false;

  var userIdEl = document.getElementById('input-userid');
  var nameEl   = document.getElementById('input-name');
  var nextBtn  = document.getElementById('login-next-btn');
  var consentEl = document.getElementById('consent-checkbox');
  var retainEl  = document.getElementById('retention-checkbox');
  var consentBtn = document.getElementById('consent-continue-btn');
  if (userIdEl) userIdEl.value      = '';
  if (nameEl)   nameEl.value        = '';
  if (nextBtn)  nextBtn.disabled    = true;
  if (consentEl) consentEl.checked  = false;
  if (retainEl)  retainEl.checked   = false;
  if (consentBtn) consentBtn.disabled = true;

  for (var i = 0; i < 3; i++) {
    var d = document.getElementById('pdot-' + i);
    if (d) d.classList.remove('done');
  }

  var verifyResult = document.getElementById('verify-result');
  if (verifyResult) {
    verifyResult.style.display = 'none';
    verifyResult.innerHTML = '';
  }

  closeVerifyLiveModal();
}

// Stop camera when user leaves page
window.addEventListener('beforeunload', function() {
  removeSource();
  closeVerifyLiveModal();
});