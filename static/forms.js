// Upload / edit form helpers: auto thumbnail from a video frame, drag & drop,
// "new category" toggle, and upload with a progress bar.
(function () {
  var form = document.querySelector('form[data-ajax]');
  if (!form) return;

  var videoInput = form.querySelector('input[name=video]');
  var thumbInput = form.querySelector('input[name=thumbnail]');
  var titleInput = form.querySelector('input[name=title]');
  var durationInput = form.querySelector('input[name=duration]');
  var removeThumb = form.querySelector('input[name=remove_thumb]');
  var preview = document.getElementById('thumb-preview');
  var emptyNote = document.getElementById('thumb-empty');
  var errorBox = form.querySelector('.form-error');
  var progress = form.querySelector('.progress');
  var submitBtn = form.querySelector('button[type=submit]');
  var thumbBlob = null;

  function showPreview(src) {
    preview.src = src;
    preview.hidden = false;
    if (emptyNote) emptyNote.hidden = true;
    if (removeThumb) removeThumb.checked = false;
  }

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = !msg;
  }

  // Draw the current frame of a <video> to a JPEG blob (max 640px wide).
  function capture(video) {
    return new Promise(function (resolve) {
      var w = video.videoWidth, h = video.videoHeight;
      if (!w || !h) return resolve(null);
      var scale = Math.min(1, 640 / w);
      var canvas = document.createElement('canvas');
      canvas.width = Math.round(w * scale);
      canvas.height = Math.round(h * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(resolve, 'image/jpeg', 0.85);
    });
  }

  function setThumbBlob(blob) {
    if (!blob) return;
    thumbBlob = blob;
    if (!(thumbInput && thumbInput.files.length)) showPreview(URL.createObjectURL(blob));
  }

  // --- category select: show the "new category" field when needed
  var catSelect = form.querySelector('[data-category-select]');
  var newCat = form.querySelector('[data-new-category]');
  if (catSelect && newCat) {
    var toggle = function () {
      newCat.hidden = catSelect.value !== '__new__';
      if (!newCat.hidden) newCat.querySelector('input').focus();
    };
    catSelect.addEventListener('change', toggle);
  }

  // --- upload page: read the chosen video locally for duration + thumbnail
  if (videoInput) {
    var zone = form.querySelector('[data-dropzone]');
    var zoneText = zone.querySelector('.dz-text');
    var defaultZoneText = zoneText.innerHTML;

    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add('drag'); });
    });
    ['dragleave', 'drop'].forEach(function (ev) {
      zone.addEventListener(ev, function () { zone.classList.remove('drag'); });
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      if (e.dataTransfer.files.length) {
        videoInput.files = e.dataTransfer.files;
        videoInput.dispatchEvent(new Event('change'));
      }
    });

    videoInput.addEventListener('change', function () {
      var file = videoInput.files[0];
      thumbBlob = null;
      durationInput.value = '';
      showError('');
      if (!file) { zoneText.innerHTML = defaultZoneText; return; }

      zoneText.textContent = file.name + ' (' + (file.size / 1048576).toFixed(1) + ' MB)';
      zone.classList.add('chosen');
      if (titleInput && !titleInput.dataset.touched) {
        titleInput.value = file.name.replace(/\.[^.]+$/, '').replace(/[_]+/g, ' ').trim();
      }

      var url = URL.createObjectURL(file);
      var v = document.createElement('video');
      v.muted = true;
      v.preload = 'auto';
      v.playsInline = true;
      v.addEventListener('loadedmetadata', function () {
        var d = v.duration;
        if (isFinite(d) && d > 0) {
          durationInput.value = d;
          v.currentTime = Math.min(Math.max(1, d * 0.1), d / 2);
        } else {
          v.currentTime = 0;
        }
      });
      v.addEventListener('seeked', function () {
        capture(v).then(function (blob) {
          setThumbBlob(blob);
          URL.revokeObjectURL(url);
        });
      }, { once: true });
      v.addEventListener('error', function () {
        URL.revokeObjectURL(url);
        if (emptyNote && !(thumbInput && thumbInput.files.length)) {
          preview.hidden = true;
          emptyNote.hidden = false;
          emptyNote.textContent = "Your browser can't preview this format, so no frame was captured. " +
            'Pick an image below, or a placeholder will be shown.';
        }
      });
      v.src = url;
    });
    if (titleInput) titleInput.addEventListener('input', function () { titleInput.dataset.touched = '1'; });
  }

  // --- edit page: grab the current frame of the player
  var frameSource = document.getElementById('frame-source');
  var useFrame = document.getElementById('use-frame');
  if (frameSource && useFrame) {
    frameSource.addEventListener('loadedmetadata', function () {
      if (isFinite(frameSource.duration)) durationInput.value = frameSource.duration;
    });
    useFrame.addEventListener('click', function () {
      capture(frameSource).then(function (blob) {
        if (!blob) return showError('Play or seek the video first, then capture a frame.');
        if (thumbInput) thumbInput.value = '';
        showError('');
        setThumbBlob(blob);
      });
    });
  }

  // --- custom image chosen
  if (thumbInput) {
    thumbInput.addEventListener('change', function () {
      if (thumbInput.files.length) showPreview(URL.createObjectURL(thumbInput.files[0]));
      else if (thumbBlob) showPreview(URL.createObjectURL(thumbBlob));
    });
  }

  // --- submit with progress
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (catSelect && catSelect.value === '__new__' && !newCat.querySelector('input').value.trim()) {
      return showError('Type a name for the new category.');
    }
    var fd = new FormData(form);
    if (!(thumbInput && thumbInput.files.length) && thumbBlob) {
      fd.set('thumbnail', thumbBlob, 'thumbnail.jpg');
    }

    var bar = progress.querySelector('.progress-bar');
    var text = progress.querySelector('.progress-text');
    var xhr = new XMLHttpRequest();
    xhr.open('POST', form.action);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.upload.addEventListener('progress', function (ev) {
      if (!ev.lengthComputable) return;
      var pct = Math.round((ev.loaded / ev.total) * 100);
      bar.style.width = pct + '%';
      text.textContent = pct < 100 ? 'Uploading… ' + pct + '%' : 'Processing…';
    });
    xhr.addEventListener('load', function () {
      var res = null;
      try { res = JSON.parse(xhr.responseText); } catch (err) { /* not JSON */ }
      if (res && res.ok) { window.location = res.redirect; return; }
      done();
      showError((res && res.error) || ('Something went wrong (HTTP ' + xhr.status + ').'));
    });
    xhr.addEventListener('error', function () {
      done();
      showError('Network error - the upload did not finish.');
    });

    function done() {
      submitBtn.disabled = false;
      progress.hidden = true;
      window.onbeforeunload = null;
    }

    showError('');
    submitBtn.disabled = true;
    progress.hidden = false;
    bar.style.width = '0%';
    text.textContent = 'Starting…';
    window.onbeforeunload = function () { return 'Upload in progress'; };
    xhr.addEventListener('load', function () { window.onbeforeunload = null; });
    xhr.send(fd);
  });
})();
