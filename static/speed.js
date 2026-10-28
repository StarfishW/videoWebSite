/* Playback speed menu: sets the player's speed and makes the Download button
   fetch a copy re-encoded at that speed (the server prepares it, we wait). */
(function () {
  var KEY = 'playbackSpeed';
  function t(s, vars) {
    s = (window.I18N && window.I18N[s]) || s;
    for (var k in (vars || {})) s = s.replace('{' + k + '}', vars[k]);
    return s;
  }
  function fmt(r) { return (Math.round(r * 100) / 100).toString(); }
  function load() { try { return parseFloat(localStorage.getItem(KEY)); } catch (e) { return NaN; } }
  function save(r) { try { localStorage.setItem(KEY, String(r)); } catch (e) { /* private mode */ } }

  var refreshers = [];
  window.refreshSpeed = function () { refreshers.forEach(function (f) { f(); }); };

  document.querySelectorAll('.speed').forEach(function (box) {
    var video = document.getElementById(box.dataset.player);
    var dl = document.getElementById(box.dataset.download);
    var min = parseFloat(box.dataset.min) || 0.25, max = parseFloat(box.dataset.max) || 4;
    var canEncode = box.dataset.encode === '1';
    var input = box.querySelector('.speed-num input');
    var range = box.querySelector('.speed-range');
    var now = box.querySelector('.speed-now');
    var buttons = box.querySelectorAll('.speed-opt');
    var label = dl ? dl.textContent.trim() : '';
    var rate = 1, busy = false;

    function clean(r) {
      r = parseFloat(r);
      if (!isFinite(r)) return null;
      return Math.round(Math.min(max, Math.max(min, r)) * 20) / 20;
    }

    function updateDownload() {
      if (!dl || busy) return;
      var url = new URL(dl.getAttribute('href'), location.href);
      url.searchParams.delete('speed');
      if (rate !== 1 && canEncode) url.searchParams.set('speed', fmt(rate));
      dl.setAttribute('href', url.pathname + url.search);
      dl.textContent = rate !== 1 && canEncode ? label + ' · ' + fmt(rate) + 'x' : label;
      dl.title = rate !== 1 && !canEncode ? t('Downloads are at normal speed (ffmpeg is not installed on the server).') : '';
    }

    function setRate(r, remember) {
      r = clean(r);
      if (r === null) { input.value = fmt(rate); return; }
      rate = r;
      if (video) { video.defaultPlaybackRate = r; video.playbackRate = r; }
      buttons.forEach(function (b) {
        var on = Math.abs(parseFloat(b.dataset.rate) - r) < 0.001;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
      if (document.activeElement !== input) input.value = fmt(r);
      range.value = r;
      now.textContent = fmt(r) + 'x';
      box.classList.toggle('custom-on', !isPreset(r));
      box.classList.toggle('changed', r !== 1);
      if (remember) save(r);
      updateDownload();
    }
    function isPreset(r) {
      for (var i = 0; i < buttons.length; i++) if (Math.abs(parseFloat(buttons[i].dataset.rate) - r) < 0.001) return true;
      return false;
    }

    buttons.forEach(function (b) {
      b.addEventListener('click', function () { setRate(b.dataset.rate, true); box.removeAttribute('open'); });
    });
    range.addEventListener('input', function () { setRate(range.value, true); });
    input.addEventListener('change', function () { setRate(input.value, true); input.value = fmt(rate); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); setRate(input.value, true); input.value = fmt(rate); box.removeAttribute('open'); }
    });
    // keep the menu on screen: it opens leftwards, unless there is no room for that
    var panel = box.querySelector('.speed-panel');
    box.addEventListener('toggle', function () {
      if (!box.open) return;
      panel.classList.remove('flip');
      if (panel.getBoundingClientRect().left < 8) panel.classList.add('flip');
    });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && box.open) { box.removeAttribute('open'); box.querySelector('summary').focus(); }
    });

    if (video) {
      // speed changed from the browser's own player menu: follow it
      video.addEventListener('ratechange', function () {
        if (Math.abs(video.playbackRate - rate) > 0.001) setRate(video.playbackRate, true);
      });
      // a new src resets the speed in some browsers
      video.addEventListener('loadedmetadata', function () { video.playbackRate = rate; });
    }

    if (dl) dl.addEventListener('click', function (e) {
      if (busy) { e.preventDefault(); return; }
      if (rate === 1 || !canEncode) return;
      e.preventDefault();
      busy = true;
      dl.classList.add('busy');
      var url = new URL(dl.getAttribute('href'), location.href);
      var status = url.pathname + '/status' + url.search;
      dl.textContent = t('Preparing {speed}x…', { speed: fmt(rate) });
      (function poll() {
        fetch(status, { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (d) {
          if (d.state === 'done') {
            busy = false; dl.classList.remove('busy'); updateDownload();
            location.href = d.url;
          } else if (d.state === 'error') {
            busy = false; dl.classList.remove('busy'); updateDownload();
            alert(t('Could not prepare the video at this speed.') + (d.error ? '\n\n' + d.error : ''));
          } else {
            dl.textContent = d.state === 'queued'
              ? t('Waiting in line…')
              : t('Preparing {speed}x… {pct}%', { speed: fmt(rate), pct: d.progress || 0 });
            setTimeout(poll, 1500);
          }
        }).catch(function () { setTimeout(poll, 4000); });
      })();
    });

    refreshers.push(function () { if (!busy) updateDownload(); if (video) video.playbackRate = rate; });

    var start = clean(new URLSearchParams(location.search).get('speed'));
    setRate(start !== null ? start : (clean(load()) || 1), start !== null);
  });
})();
