/* PERSISTENCE — PGlite, pgWrite/pgRead, saveState patch */

async function initPGlite() {
  try {
    var mod = await import('https://cdn.jsdelivr.net/npm/@electric-sql/pglite/dist/index.js');
    var PGlite = mod.PGlite || mod.default;
    _pgdb = new PGlite('idb://rockoagents_v1');
    await _pgdb.exec(`
      CREATE TABLE IF NOT EXISTS kv (
        key   TEXT PRIMARY KEY,
        value TEXT
      );
      CREATE TABLE IF NOT EXISTS companies (
        id          TEXT PRIMARY KEY,
        user_id     TEXT,
        active      INTEGER DEFAULT 0,
        data        TEXT
      );
      CREATE TABLE IF NOT EXISTS agents (
        id          TEXT PRIMARY KEY,
        company_id  TEXT,
        project     TEXT,
        data        TEXT
      );
      CREATE TABLE IF NOT EXISTS run_history (
        id          TEXT PRIMARY KEY,
        project     TEXT,
        data        TEXT,
        created_at  TEXT
      );
    `);
    _pgReady = true;
    // Flush queued writes
    for (var fn of _pgQueue) { try { await fn(); } catch(e) {} }
    _pgQueue = [];
    // Migrate from localStorage on first run
    await _pgMigrateFromLocalStorage();
    RockoCore.log('info', 'PGlite ready — unlimited persistent storage active');
    return true;
  } catch(e) {
    console.warn('PGlite unavailable, falling back to localStorage:', e.message);
    _pgReady = false;
    return false;
  }
}

async function _pgMigrateFromLocalStorage() {
  // One-time migration: move companies from localStorage to PGlite
  var migrated = await _pgGet('_migrated_v1');
  if (migrated) return;
  try {
    var raw = localStorage.getItem(COMPANIES_KEY);
    if (raw) {
      var list = filterDeletedCompanies(JSON.parse(raw));
      for (var co of list) {
        await _pgSaveCompany(co);
      }
      if (list.length) {
        RockoCore.log('info', 'Migrated ' + list.length + ' company/companies from localStorage to PGlite');
        localStorage.removeItem(COMPANIES_KEY);
      }
    }
    // Migrate RockoCore state (agents, project config)
    var stateKey = 'rockoagents_v4';
    var stateRaw = localStorage.getItem(stateKey);
    if (stateRaw) {
      await _pgSet('rockocore_state', stateRaw);
      RockoCore.log('info', 'Migrated RockoCore state to PGlite');
    }
  } catch(e) {
    console.warn('Migration error:', e);
  }
  await _pgSet('_migrated_v1', 'true');
}

// ── PGlite KV helpers ─────────────────────────────────────────
async function _pgGet(key) {
  if (!_pgReady || !_pgdb) return null;
  try {
    var r = await _pgdb.query('SELECT value FROM kv WHERE key = $1', [key]);
    return r.rows.length ? r.rows[0].value : null;
  } catch { return null; }
}
async function _pgSet(key, value) {
  if (!_pgReady || !_pgdb) return false;
  try {
    await _pgdb.exec(
      'INSERT INTO kv (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value',
      [key, typeof value === 'string' ? value : JSON.stringify(value)]
    );
    return true;
  } catch { return false; }
}
async function _pgDelete(key) {
  if (!_pgReady || !_pgdb) return;
  try { await _pgdb.exec('DELETE FROM kv WHERE key = $1', [key]); } catch {}
}

// ── Company persistence via PGlite ────────────────────────────
async function _pgSaveCompany(co) {
  if (!_pgReady || !_pgdb) return;
  var lean = Object.assign({}, co);
  delete lean._manifest;
  try {
    await _pgdb.exec(
      'INSERT INTO companies (id, user_id, active, data) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO UPDATE SET user_id=EXCLUDED.user_id, active=EXCLUDED.active, data=EXCLUDED.data',
      [co.id, co.user_id||'', co.active ? 1 : 0, JSON.stringify(lean)]
    );
  } catch(e) { console.warn('PGlite company save:', e); }
}
async function _pgLoadCompanies() {
  if (!_pgReady || !_pgdb) return null;
  try {
    var r = await _pgdb.query('SELECT data, active FROM companies ORDER BY active DESC');
    return filterDeletedCompanies(r.rows.map(function(row) {
      var co = JSON.parse(row.data);
      co.active = row.active === 1;
      return co;
    }));
  } catch { return null; }
}
async function _pgDeleteCompany(id) {
  if (!_pgReady || !_pgdb) return;
  try { await _pgdb.exec('DELETE FROM companies WHERE id = $1', [id]); } catch {}
}

// ── Override getCompanies / saveCompanies to use PGlite ──────────────────────
// Store a sync cache for immediate reads
var _companiesCache = null;

var _origGetCompanies = getCompanies;
getCompanies = function() {
  // Return cache if available (PGlite is async, cache is sync)
  if (_companiesCache !== null) return _companiesCache;
  // Fallback to localStorage during init
  try { return JSON.parse(localStorage.getItem(COMPANIES_KEY) || '[]'); }
  catch { return []; }
};

var _origSaveCompanies = saveCompanies;
saveCompanies = function(list) {
  list = filterDeletedCompanies(list || []);
  var lean = (list || []).map(function(c){
    var x = Object.assign({}, c);
    delete x._manifest;
    return x;
  });

  _companiesCache = list || [];

  try {
    localStorage.setItem(COMPANIES_KEY, JSON.stringify(lean));
  } catch(e) {
    try {
      localStorage.removeItem(COMPANIES_KEY);
      localStorage.setItem(COMPANIES_KEY, JSON.stringify(lean));
    } catch(e2) {
      console.warn('localStorage quota exceeded');
    }
  }

  if (_pgReady && _pgdb) {
    (async function(){
      try {
        await _pgdb.exec('DELETE FROM companies');
        for (var i = 0; i < (list || []).length; i++) {
          await _pgSaveCompany(list[i]);
        }
      } catch(e) {
        console.warn('PGlite full company save failed:', e);
      }
    })();
  }

  bridgePost('/data/save', {key:'companies', data:lean}).catch(function(){});
};

// Also persist RockoCore state to PGlite on every save
var _origSaveState = null;
function _hookRockoCoreSave() {
  if (!window.RockoCore || !_pgReady) return;
  // Intercept localStorage.setItem for the state key
  var _origSetItem = localStorage.setItem.bind(localStorage);
  localStorage.setItem = function(key, value) {
    _origSetItem(key, value);
    if (key === 'rockoagents_v4' && _pgReady) {
      _pgSet('rockocore_state', value).catch(function(){});
    }
  };
}

// ── Restore RockoCore state from PGlite on load ───────────────
async function _pgRestoreState() {
  if (!_pgReady) return false;
  // Restore companies
  var pgCompanies = await _pgLoadCompanies();
  if (pgCompanies && pgCompanies.length) {
    _companiesCache = pgCompanies;
  }
  // Restore RockoCore state if localStorage is empty
  var stateKey = 'rockoagents_v4';
  if (!localStorage.getItem(stateKey)) {
    var pgState = await _pgGet('rockocore_state');
    if (pgState) {
      try {
        localStorage.setItem(stateKey, pgState);
        RockoCore.log('info', 'State restored from PGlite');
      } catch {}
    }
  }
  return true;
}

// ── Boot PGlite ───────────────────────────────────────────────
async function bootPGlite() {
  var ok = await initPGlite();
  if (ok) {
    await _pgRestoreState();
    _hookRockoCoreSave();
  }
  return ok;
}

// ── Boot sequence ────────────────────────────────────────────

// ═══════════════════════════════════════════════════════════════
// PGLITE STORAGE LAYER
// Replaces localStorage as durable store. No size limit.
// localStorage stays as sync fast-path / cache.
// PGlite (Postgres in WASM) is the source of truth.
// ═══════════════════════════════════════════════════════════════
var _pgDb     = null;
var _pgReady  = false;
var _pgQueue  = [];   // writes queued before PGlite is ready







function pgWrite(key, value) {
  // Sync fast-path: localStorage
  try { localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value)); }
  catch(e) {
    // Quota exceeded — clear old cache and continue
    try {
      var keep = ['rockoagents_session_v1','rockoagents_active_co',DELETED_COMPANIES_KEY];
      for (var i = localStorage.length - 1; i >= 0; i--) {
        var k = localStorage.key(i);
        if (k && !keep.includes(k) && k !== key) localStorage.removeItem(k);
      }
      localStorage.setItem(key, typeof value === 'string' ? value : JSON.stringify(value));
    } catch(e2) { console.warn('localStorage full, PGlite only'); }
  }
  // Async durable write to PGlite
  if (_pgReady) {
    _pgSet(key, value);
  } else {
    _pgQueue.push({key: key, val: value});
  }
}

async function pgRead(key) {
  // Try localStorage first (fast sync path)
  var ls = localStorage.getItem(key);
  if (ls) { try { return JSON.parse(ls); } catch { return ls; } }
  // Fall back to PGlite (durable — survives localStorage clear)
  if (_pgReady) {
    return await _pgGet(key);
  }
  return null;
}



// Override RockoCore saveState to use pgWrite
// This is called after RockoCore is defined
function patchRockoCorePersistence() {
  if (!window.RockoCore) return;
  var _origSave = RockoCore.saveState;
  if (_origSave && !_origSave._patched) {
    RockoCore.saveState = function() {
      var result = _origSave ? _origSave.apply(this, arguments) : null;
      // Also write via pgWrite for quota protection
      try {
        var state = RockoCore.exportState ? RockoCore.exportState() : null;
        if (state) pgWrite('rockoagents_v4', state);
      } catch(e) {}
      return result;
    };
    RockoCore.saveState._patched = true;
  }
}