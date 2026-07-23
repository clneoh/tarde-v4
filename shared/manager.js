var SHARED_URL = '/tarde-v4/shared/shared.json';
var RAW_URL = 'https://raw.githubusercontent.com/clneoh/tarde-v4/main/shared/shared.json';
var ALL_TF = ['M1','W1','D1','D2','H4','H1','m15'];
var TF_RANK = {M1:0,W1:1,D1:2,D2:3,H4:4,H1:5,m15:6};
var assetList = {};

function load() {
  // Auto-fill token from storage
  try { var saved = localStorage.getItem('gh_pat'); if (saved) document.getElementById('ghToken').value = saved; } catch(e) {}
  fetch(RAW_URL + '?t=' + Date.now())
    .then(function(r) { return r.json(); })
    .then(function(data) {
      assetList = data.asset_list || {};
      render();
    })
    .catch(function() {
      document.getElementById('stats').textContent = 'Failed to load';
    });
}

function render() {
  var keys = Object.keys(assetList);
  var v4c = 0, pamc = 0;
  for (var i = 0; i < keys.length; i++) {
    if (assetList[keys[i]].enabled_v4) v4c++;
    if (assetList[keys[i]].enabled_pam) pamc++;
  }
  document.getElementById('stats').textContent = keys.length + ' assets - V4: ' + v4c + ' - PAM: ' + pamc;

  var html = '';
  for (var ki = 0; ki < keys.length; ki++) {
    var ticker = keys[ki];
    var a = assetList[ticker];
    var bias = a.bias_tf || [];
    var exec = a.exec_tf || '';
    var execRank = TF_RANK[exec] != null ? TF_RANK[exec] : 99;

    var biasHTML = '';
    for (var ti = 0; ti < ALL_TF.length; ti++) {
      var tf = ALL_TF[ti];
      if (tf === exec) continue; // skip exec TF in bias column
      var on = bias.indexOf(tf) >= 0;
      var tfRank = TF_RANK[tf] != null ? TF_RANK[tf] : 99;
      var blocked = tfRank >= execRank; // same or lower than exec
      var cls = 'tf-tag ' + (on ? 'on' : 'off') + (blocked ? ' disabled' : '');
      var dattr = blocked ? '' : ' data-action="bias" data-ticker="' + ticker + '" data-tf="' + tf + '"';
      biasHTML += '<span class="' + cls + '"' + dattr + '>' + tf + '</span>';
    }

    var execHTML = '';
    for (var ei = 0; ei < ALL_TF.length; ei++) {
      var etf = ALL_TF[ei];
      var isExec = exec === etf;
      var ecls = 'tf-tag ' + (isExec ? 'exec' : 'off');
      execHTML += '<span class="' + ecls + '" data-action="exec" data-ticker="' + ticker + '" data-tf="' + etf + '">' + etf + '</span>';
    }

    html += '<tr>' +
      '<td style="font-weight:700;color:#ffd740;white-space:nowrap">' + ticker + '</td>' +
      '<td>' + (a.exchange||'?') + '</td>' +
      '<td>' + (a.type||'?') + '</td>' +
      '<td>' + (a.session||'?') + '</td>' +
      '<td>' + biasHTML + '</td>' +
      '<td>' + execHTML + '</td>' +
      '<td style="text-align:center"><span class="toggle ' + (a.enabled_v4?'on':'off') + '" data-action="toggle" data-ticker="' + ticker + '" data-field="enabled_v4">' + (a.enabled_v4?'o':'x') + '</span></td>' +
      '<td style="text-align:center"><span class="toggle ' + (a.enabled_pam?'on':'off') + '" data-action="toggle" data-ticker="' + ticker + '" data-field="enabled_pam">' + (a.enabled_pam?'o':'x') + '</span></td>' +
      '<td><button class="btn btn-rm" data-action="remove" data-ticker="' + ticker + '">x</button></td>' +
    '</tr>';
  }
  document.getElementById('assetTable').innerHTML = html || '<tr><td colspan="9" style="color:#555;text-align:center;padding:20px">No assets</td></tr>';
}

document.getElementById('assetTable').addEventListener('click', function(e) {
  var el = e.target.closest('[data-action]');
  if (!el) return;
  var action = el.getAttribute('data-action');
  var ticker = el.getAttribute('data-ticker');
  if (action === 'bias') toggleBias(ticker, el.getAttribute('data-tf'));
  else if (action === 'exec') setExec(ticker, el.getAttribute('data-tf'));
  else if (action === 'toggle') toggleModel(ticker, el.getAttribute('data-field'));
  else if (action === 'remove') removeAsset(ticker);
});

function toggleBias(ticker, tf) {
  var a = assetList[ticker];
  var bias = a.bias_tf || [];
  var idx = bias.indexOf(tf);
  if (idx >= 0) bias.splice(idx, 1);
  else bias.push(tf);
  a.bias_tf = bias;
  render();
}

function setExec(ticker, tf) {
  var a = assetList[ticker];
  a.exec_tf = a.exec_tf === tf ? '' : tf;
  if (a.exec_tf) {
    var execRank = TF_RANK[a.exec_tf] != null ? TF_RANK[a.exec_tf] : 99;
    a.bias_tf = (a.bias_tf || []).filter(function(btf) { return (TF_RANK[btf] != null ? TF_RANK[btf] : 99) < execRank; });
  }
  render();
}

function toggleModel(ticker, field) {
  assetList[ticker][field] = !assetList[ticker][field];
  render();
}

function addAsset() {
  var t = document.getElementById('newTicker').value.toUpperCase().trim();
  if (!t) return;
  assetList[t] = {
    exchange: document.getElementById('newExchange').value.trim(),
    type: document.getElementById('newType').value,
    session: document.getElementById('newSession').value,
    bias_tf: [], exec_tf: '',
    enabled_v4: true, enabled_pam: true
  };
  document.getElementById('newTicker').value = '';
  document.getElementById('newExchange').value = '';
  render();
}

function removeAsset(ticker) {
  if (confirm('Remove ' + ticker + '?')) { delete assetList[ticker]; render(); }
}

function buildConfig() {
  var out = {};
  var keys = Object.keys(assetList);
  for (var i = 0; i < keys.length; i++) {
    var k = keys[i], v = assetList[k];
    out[k] = { exchange: v.exchange, type: v.type, session: v.session, bias_tf: v.bias_tf||[], exec_tf: v.exec_tf||'', enabled_v4: v.enabled_v4, enabled_pam: v.enabled_pam };
  }
  return { assets: out };
}

function showPreview() {
  document.getElementById('output').style.display = 'block';
  document.getElementById('output').textContent = JSON.stringify(buildConfig(), null, 2);
}

function setStatus(msg, ok) {
  var s = document.getElementById('status');
  s.textContent = msg;
  s.className = 'status ' + (ok ? 'ok' : 'err');
  if (ok) setTimeout(function() { s.textContent = ''; }, 3000);
}

function unfreeze() {
  // Reload fresh data after pipeline
  fetch(RAW_URL + '?t=' + Date.now())
    .then(function(r){ return r.json(); })
    .then(function(d){ assetList = d.asset_list || {}; render(); })
    .catch(function(){});

  document.body.style.pointerEvents = '';
  document.body.style.opacity = '1';
  document.getElementById('applyBtn').textContent = 'Apply Changes';
  document.getElementById('applyBtn').disabled = false;
}


function applyConfig() {
  var token = document.getElementById('ghToken').value.trim();
  if (token) { try { localStorage.setItem('gh_pat', token); } catch(e) {} }
  if (!token) { setStatus('Enter GitHub PAT first', false); return; }
  var btn = document.getElementById('applyBtn');
  btn.textContent = 'Applying...'; btn.disabled = true;
  setStatus('Pushing...', true);

  var api = 'https://api.github.com/repos/clneoh/tarde-v4/contents/shared/shared_config.json';
  var headers = { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json' };

  fetch(api, { headers: headers, cache: 'no-store' })
    .then(function(r) { if (!r.ok) throw new Error('Fetch: ' + r.status); return r.json(); })
    .then(function(file) {
      var config = JSON.parse(atob(file.content));
      config.assets = buildConfig().assets;
      return fetch(api, {
        method: 'PUT',
        headers: headers,
        body: JSON.stringify({ message: 'update assets from manager', content: btoa(unescape(encodeURIComponent(JSON.stringify(config, null, 2)))), sha: file.sha })
      });
    })
    .then(function(r) {
      if (!r.ok) throw new Error('Push: ' + r.status);
      btn.textContent = 'Apply Changes'; btn.disabled = false;
      // Freeze entire page
        document.body.style.pointerEvents = 'none';
        document.body.style.opacity = '0.6';
        
        setStatus('Applying... 0s', true);
        fetch('http://54.254.254.195:8765/trigger', {mode:'no-cors'}).catch(function(){});
        var elapsed = 0;
        var timer = setInterval(function() {
          elapsed++;
          if (elapsed < 10) setStatus('Applying... ' + elapsed + 's', true);
          else if (elapsed < 30) setStatus('Applying... ' + elapsed + 's (fetching data)', true);
          else if (elapsed < 60) setStatus('Applying... ' + elapsed + 's (almost done)', true);
          else { clearInterval(timer); unfreeze(); }
        }, 1000);
        setTimeout(function() { clearInterval(timer); unfreeze(); }, 65000);
        _applied = true;
    })
    .catch(function(e) {
      console.error('Apply failed:', e.message);
      alert('Apply failed: ' + e.message + '\n\nGet PAT at https://github.com/settings/tokens\nScope: repo');
      btn.textContent = 'Apply Changes'; btn.disabled = false;
      setStatus(e.message, false);
    });
}

load();