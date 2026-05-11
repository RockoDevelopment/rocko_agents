/* AUTH — session, login, signup, auth helpers */

var SESSION_KEY   = 'rockoagents_session_v1';
var _currentUser  = null;
var _sessionToken = null;

function getSessionToken() {
  return localStorage.getItem(SESSION_KEY) || null;
}
function setSessionToken(token) {
  _sessionToken = token;
  if (token) localStorage.setItem(SESSION_KEY, token);
  else        localStorage.removeItem(SESSION_KEY);
}
function getAuthHeaders() {
  var token = getSessionToken();
  return token ? {'Content-Type':'application/json','Authorization':'Bearer '+token}
               : {'Content-Type':'application/json'};
}

async function authGet(path) {
  try {
    var r = await fetch(BRIDGE + path, {
      headers: getAuthHeaders(), signal: AbortSignal.timeout(5000)
    });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}
async function authPost(path, body) {
  try {
    var r = await fetch(BRIDGE + path, {
      method:'POST', headers: getAuthHeaders(),
      body: JSON.stringify(body), signal: AbortSignal.timeout(8000)
    });
    return await r.json();
  } catch(e) { return {error: e.message}; }
}

// ── UI helpers ────────────────────────────────────────────────
function showLoginOverlay()  { var o=document.getElementById('loginOverlay');  if(o) o.classList.add('show'); }
function hideLoginOverlay()  { var o=document.getElementById('loginOverlay');  if(o) o.classList.remove('show'); }
function showLogin()  { document.getElementById('loginForm').style.display=''; document.getElementById('signupForm').style.display='none'; clearLoginErrors(); }
function showSignup() { document.getElementById('signupForm').style.display=''; document.getElementById('loginForm').style.display='none'; clearLoginErrors(); }
function clearLoginErrors() {
  ['loginError','signupError'].forEach(function(id){ var e=document.getElementById(id); if(e){e.textContent='';e.classList.remove('show');} });
}
function showLoginError(id, msg) {
  var e = document.getElementById(id); if(!e) return;
  e.textContent = msg; e.classList.add('show');
}
function setLoginLoading(btnId, loading) {
  var btn = document.getElementById(btnId); if(!btn) return;
  btn.disabled = loading; btn.textContent = loading ? 'Please wait...' : (btnId==='loginBtn'?'Sign In':'Create Account');
}
function updateTopbarUser(user) {
  var av   = document.getElementById('topbarUserAvatar');
  var info = document.getElementById('topbarUserInfo');
  var name = document.getElementById('topbarUserName');
  var lout = document.getElementById('logoutBtn');
  if(!user) { if(av) av.style.display='none'; if(info) info.style.display='none'; if(lout) lout.style.display='none'; return; }
  if(av)   { av.style.display='flex'; av.textContent=(user.name||user.email||'?').charAt(0).toUpperCase(); }
  if(info) { info.style.display='block'; }
  if(name) { name.textContent=user.name||user.email; }
  if(lout) { lout.style.display=''; }
}

// ── Enter key support ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
  ['loginPassword','signupPassword'].forEach(function(id) {
    var el = document.getElementById(id); if(!el) return;
    el.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { id==='loginPassword' ? submitLogin() : submitSignup(); }
    });
  });
});

// ── Submit handlers ───────────────────────────────────────────
async function submitLogin() {
  var email = (document.getElementById('loginEmail').value||'').trim();
  var pass  = (document.getElementById('loginPassword').value||'');
  if (!email || !pass) { showLoginError('loginError','Email and password are required'); return; }
  clearLoginErrors(); setLoginLoading('loginBtn', true);
  var res = await authPost('/auth/login', {email, password: pass});
  setLoginLoading('loginBtn', false);
  if (res && res.token) {
    setSessionToken(res.token);
    _currentUser = res.user;
    hideLoginOverlay();
    updateTopbarUser(res.user);
    await bootCompanyLayer();
    var _sh = document.getElementById('appShell');
    if (_sh) _sh.classList.add('auth-ready');
  } else if (!res) {
    showLoginError('loginError', 'Bridge is not running — start Rocko Development first (rocko.exe run)');
  } else if (res.detail && res.detail.toString().includes('404')) {
    showLoginError('loginError', 'Bridge needs to be updated — rebuild rocko.exe from GitHub Actions');
  } else {
    showLoginError('loginError', (res && res.detail) || 'Invalid email or password');
  }
}

async function submitSignup() {
  var name  = (document.getElementById('signupName').value||'').trim();
  var email = (document.getElementById('signupEmail').value||'').trim();
  var pass  = (document.getElementById('signupPassword').value||'');
  if (!email || !pass) { showLoginError('signupError','Email and password are required'); return; }
  if (pass.length < 6) { showLoginError('signupError','Password must be at least 6 characters'); return; }
  clearLoginErrors(); setLoginLoading('signupBtn', true);
  var res = await authPost('/auth/signup', {name, email, password: pass});
  setLoginLoading('signupBtn', false);
  if (res && res.token) {
    setSessionToken(res.token);
    _currentUser = res.user;
    hideLoginOverlay();
    updateTopbarUser(res.user);
    await bootCompanyLayer();
    var _sh = document.getElementById('appShell');
    if (_sh) _sh.classList.add('auth-ready');
  } else if (!res) {
    showLoginError('signupError', 'Bridge offline — run rocko.exe run first');
  } else if (res.status === 404 || (res.detail && res.detail.toString().includes('404'))) {
    showLoginError('signupError', 'Old exe detected — rebuild rocko.exe from GitHub Actions to get auth support');
  } else {
    showLoginError('signupError', (res && res.detail) || 'Could not create account');
  }
}

async function doLogout() {
  if (!confirm('Sign out of Rocko Development?')) return;
  await authPost('/auth/logout', {});
  setSessionToken(null);
  _currentUser = null;
  updateTopbarUser(null);
  // Clear company state
  _currentCompanyLogo = null;
  renderCompanyRail();
  updateTopbarCompany(null);
  showLoginOverlay();
  showLogin();
}

// Override bridgePost to always include auth header
var _bridgePostBase = bridgePost;
bridgePost = async function(path, body) {
  try {
    var headers = getAuthHeaders();
    var r = await fetch(BRIDGE + path, {
      method:'POST', headers: headers,
      body: JSON.stringify(body||{}), signal: AbortSignal.timeout(8000)
    });
    return r.ok ? await r.json() : null;
  } catch { return null; }
};
var _bridgeGetBase = bridgeGet;
bridgeGet = async function(path) {
  try {
    var r = await fetch(BRIDGE + path, {headers: getAuthHeaders(), signal: AbortSignal.timeout(5000)});
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
};

// ── Export / Import account ───────────────────────────────────
async function exportAccount() {
  showLoading('Exporting account...');
  var d = await authGet('/auth/export');
  hideLoading();
  if (!d) { toastErr('Export failed — sign in and ensure bridge is running'); return; }
  var blob = new Blob([JSON.stringify(d, null, 2)], {type:'application/json'});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'rockoagents_account_backup_' + new Date().toISOString().split('T')[0] + '.json';
  a.click();
  toastOk('Account exported — keep this file safe');
}

// ── Boot auth ─────────────────────────────────────────────────
async function bootAuth() {
  var token = getSessionToken();
  if (!token) { showLoginOverlay(); return false; }
  // Validate session with bridge
  var res = await authGet('/auth/me');
  if (res && res.user) {
    _currentUser = res.user;
    _sessionToken = token;
    updateTopbarUser(res.user);
    return true;
  }
  // Token invalid/expired
  setSessionToken(null);
  showLoginOverlay();
  return false;
}


// ═══════════════════════════════════════════════════════════════
// PGLITE — PostgreSQL in WebAssembly
// Replaces localStorage as the primary data store.
// Uses IndexedDB backend (idb://) — no size limits, survives
// page refreshes and browser-data clears that wipe localStorage.
// localStorage kept only for: session token + active company id.
// ═══════════════════════════════════════════════════════════════
var _pgdb   = null;
var _pgReady = false;
var _pgQueue = [];   // queued writes while initialising