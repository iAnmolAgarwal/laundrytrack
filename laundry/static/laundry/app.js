/* LaundryTrack: small progressive enhancements, no dependencies */
(function () {
  'use strict';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function toInt(v) { var n = parseInt(v, 10); return isNaN(n) || n < 0 ? 0 : n; }

  /* Dismissible flash messages */
  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-dismiss]');
    if (btn) {
      var box = btn.closest('[data-dismissible]');
      if (box) box.remove();
    }
  });

  /* Confirm before destructive POSTs */
  $all('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!window.confirm(form.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  /* Slip photo kept in this browser tab only (sessionStorage) */
  var PHOTO_PREFIX = 'laundrytrack:photo:';
  var MAX_ORIGINAL_BYTES = 4 * 1024 * 1024;
  var MAX_SIDE = 1600;
  var JPEG_QUALITY = 0.82;

  function storageGet(key) {
    try { return window.sessionStorage.getItem(key); } catch (err) { return null; }
  }
  function storageSet(key, value) {
    try { window.sessionStorage.setItem(key, value); return true; } catch (err) { return false; }
  }
  function clearStoredPhotos() {
    try {
      var store = window.sessionStorage;
      for (var i = store.length - 1; i >= 0; i--) {
        var k = store.key(i);
        if (k && k.indexOf(PHOTO_PREFIX) === 0) store.removeItem(k);
      }
    } catch (err) { /* storage blocked */ }
  }
  function randomKey() {
    var chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    var out = '';
    var bytes = null;
    try {
      bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
    } catch (err) { bytes = null; }
    for (var i = 0; i < 16; i++) {
      var n = bytes ? bytes[i] : Math.floor(Math.random() * 256);
      out += chars.charAt(n % chars.length);
    }
    return out;
  }

  /* Lightbox zoom for slip photos (delegated so late-filled photos work) */
  var lightbox = $('[data-lightbox]');
  if (lightbox) {
    var lbImg = $('[data-lightbox-img]', lightbox);
    var closeLb = function () { lightbox.hidden = true; document.body.style.overflow = ''; };
    document.addEventListener('click', function (e) {
      var el = e.target.closest('[data-zoom]');
      if (!el || !el.getAttribute('data-zoom')) return;
      lbImg.src = el.getAttribute('data-zoom');
      lightbox.hidden = false;
      document.body.style.overflow = 'hidden';
      $('[data-lightbox-close]', lightbox).focus();
    });
    lightbox.addEventListener('click', function (e) {
      if (e.target === lightbox || e.target.closest('[data-lightbox-close]')) closeLb();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !lightbox.hidden) closeLb();
    });
  }

  /* Upload: shrink in the browser, preview, loading state */
  var uploadForm = $('[data-upload-form]');
  if (uploadForm) {
    var zone = $('[data-dropzone]', uploadForm);
    var input = $('[data-file-input]', uploadForm);
    var keyInput = $('[data-photo-key]', uploadForm);
    var empty = $('[data-dropzone-empty]', uploadForm);
    var preview = $('[data-dropzone-preview]', uploadForm);
    var previewImg = $('[data-preview-img]', uploadForm);
    var fileName = $('[data-file-name]', uploadForm);
    var fileSize = $('[data-file-size]', uploadForm);
    var fileError = $('[data-file-error]', uploadForm);
    var submitBtn = $('[data-submit]', uploadForm);
    var loading = $('[data-loading]');
    var formDisabled = uploadForm.hasAttribute('data-disabled');
    var SUBMIT_LABEL = 'Read slip';

    var processing = false;
    var blocked = false;
    var runId = 0;
    var previewUrl = null;
    var swapping = false;

    var setError = function (msg) {
      blocked = !!msg;
      if (!fileError) return;
      fileError.textContent = msg || '';
      fileError.hidden = !msg;
    };
    var setBusy = function (busy) {
      processing = busy;
      if (formDisabled) return;
      submitBtn.disabled = busy;
      submitBtn.textContent = busy ? 'Preparing photo…' : SUBMIT_LABEL;
    };
    var showPreview = function (file) {
      if (previewUrl && window.URL) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
      if (!file) {
        empty.hidden = false; preview.hidden = true; zone.classList.remove('has-file');
        return;
      }
      fileName.textContent = file.name;
      fileSize.textContent = (file.size / 1024 / 1024).toFixed(1) + ' MB';
      if (window.URL && file.type && file.type.indexOf('image/') === 0) {
        previewUrl = URL.createObjectURL(file);
        previewImg.hidden = false;
        previewImg.onerror = function () { previewImg.hidden = true; };
        previewImg.src = previewUrl;
      } else {
        previewImg.hidden = true;
      }
      empty.hidden = true;
      preview.hidden = false;
      zone.classList.add('has-file');
    };

    var decodeImage = function (file) {
      return new Promise(function (resolve, reject) {
        if (!window.URL) { reject(new Error('no URL')); return; }
        var url = URL.createObjectURL(file);
        var img = new Image();
        img.onload = function () {
          if (!img.naturalWidth || !img.naturalHeight) { URL.revokeObjectURL(url); reject(new Error('empty')); return; }
          resolve({ img: img, url: url });
        };
        img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('decode')); };
        img.src = url;
      });
    };
    var shrink = function (file) {
      return decodeImage(file).then(function (res) {
        var img = res.img;
        var w = img.naturalWidth, h = img.naturalHeight;
        var scale = Math.min(1, MAX_SIDE / Math.max(w, h));
        var cw = Math.max(1, Math.round(w * scale));
        var ch = Math.max(1, Math.round(h * scale));
        var canvas = document.createElement('canvas');
        canvas.width = cw; canvas.height = ch;
        var ctx = canvas.getContext('2d');
        if (!ctx) { URL.revokeObjectURL(res.url); throw new Error('no canvas'); }
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, cw, ch);
        ctx.drawImage(img, 0, 0, cw, ch);
        URL.revokeObjectURL(res.url);
        return new Promise(function (resolve, reject) {
          if (!canvas.toBlob) { reject(new Error('no toBlob')); return; }
          canvas.toBlob(function (blob) {
            if (!blob) { reject(new Error('encode')); return; }
            resolve(blob);
          }, 'image/jpeg', JPEG_QUALITY);
        });
      });
    };
    var blobToDataUrl = function (blob) {
      return new Promise(function (resolve, reject) {
        var reader = new FileReader();
        reader.onload = function () { resolve(String(reader.result || '')); };
        reader.onerror = function () { reject(reader.error); };
        reader.readAsDataURL(blob);
      });
    };
    var replaceInputFile = function (file) {
      if (typeof window.DataTransfer !== 'function') return false;
      try {
        var dt = new DataTransfer();
        dt.items.add(file);
        swapping = true;
        input.files = dt.files;
        swapping = false;
        return !!(input.files && input.files[0] === dt.files[0] && input.files[0].name === file.name);
      } catch (err) { swapping = false; return false; }
    };
    var fallbackToOriginal = function (original) {
      showPreview(original);
      if (original.size > MAX_ORIGINAL_BYTES) {
        setError('This photo is too large. Take it again or pick a JPEG.');
      } else {
        setError('');
      }
    };

    var handleFile = function () {
      var original = input.files && input.files[0];
      var myRun = ++runId;
      setError('');
      if (!original) { if (keyInput) keyInput.value = ''; showPreview(null); setBusy(false); return; }

      var key = randomKey();
      if (keyInput) keyInput.value = key;
      showPreview(original);
      setBusy(true);

      shrink(original).then(function (blob) {
        if (myRun !== runId) return null;
        var small;
        try { small = new File([blob], 'slip.jpg', { type: 'image/jpeg', lastModified: Date.now() }); }
        catch (err) { small = null; }
        if (!small || !replaceInputFile(small)) {
          // Cannot swap the file in this browser: upload the original as is
          fallbackToOriginal(original);
          return null;
        }
        showPreview(small);
        return blobToDataUrl(blob).then(function (dataUrl) {
          if (myRun !== runId || !dataUrl) return;
          clearStoredPhotos();
          storageSet(PHOTO_PREFIX + key, dataUrl);
        });
      }).catch(function () {
        // Could not decode (for example HEIC in Chrome): upload the original
        if (myRun !== runId) return;
        fallbackToOriginal(original);
      }).then(function () {
        if (myRun === runId) setBusy(false);
      });
    };
    input.addEventListener('change', function () { if (!swapping) handleFile(); });
    if (input.files && input.files.length) handleFile();

    ['dragenter', 'dragover'].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); if (!formDisabled) zone.classList.add('is-drag'); });
    });
    ['dragleave', 'dragend', 'drop'].forEach(function (ev) {
      zone.addEventListener(ev, function () { zone.classList.remove('is-drag'); });
    });
    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      if (formDisabled) return;
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        try { input.files = e.dataTransfer.files; } catch (err) { return; }
        handleFile();
      }
    });

    uploadForm.addEventListener('submit', function (e) {
      if (formDisabled || processing) { e.preventDefault(); return; }
      if (!input.files || !input.files.length) {
        e.preventDefault();
        setError('Choose a photo first.');
        input.focus();
        return;
      }
      if (blocked) {
        e.preventDefault();
        if (fileError) fileError.scrollIntoView({ block: 'center' });
        return;
      }
      submitBtn.disabled = true;
      submitBtn.classList.add('is-loading');
      submitBtn.textContent = 'Reading slip…';
      if (loading) loading.hidden = false;
    });
    // Restore the button if the user navigates back (bfcache)
    window.addEventListener('pageshow', function () {
      submitBtn.classList.remove('is-loading');
      if (loading) loading.hidden = true;
      if (!formDisabled) setBusy(processing);
    });
  }

  /* Review: digital slip, custom rows, live total, email preview, photo */
  var reviewForm = $('[data-review-form]');
  if (reviewForm) {
    var rows = $('[data-item-rows]', reviewForm);
    var tpl = $('[data-row-template]', reviewForm);
    var totalEl = $('[data-total]', reviewForm);
    var anchor = $('[data-add-anchor]', reviewForm);

    // Colour cells grow to fit long values instead of clipping them
    var fitText = function (t) {
      t.style.height = 'auto';
      t.style.height = t.scrollHeight + 'px';
    };
    var fitAll = function () { $all('textarea.slip-colour', reviewForm).forEach(fitText); };

    var updateTotal = function () {
      var sum = 0;
      $all('input[name="item_qty"]', rows).forEach(function (q) { sum += toInt(q.value); });
      if (totalEl) totalEl.textContent = sum;
    };
    var addRow = function () {
      if (!tpl || !tpl.content) return;
      var node = tpl.content.querySelector('tr').cloneNode(true);
      if (anchor && anchor.parentNode === rows) rows.insertBefore(node, anchor);
      else rows.appendChild(node);
      var first = node.querySelector('input');
      if (first) first.focus();
      $all('textarea', node).forEach(fitText);
      updateTotal();
    };

    $all('[data-add-row]', reviewForm).forEach(function (b) { b.addEventListener('click', addRow); });
    if (rows) {
      rows.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-remove-row]');
        if (!btn) return;
        var row = btn.closest('[data-item-row]');
        if (row) row.remove();
        updateTotal();
        var addBtn = $('[data-add-row]', reviewForm);
        if (addBtn) addBtn.focus();
      });
      rows.addEventListener('input', function (e) {
        if (e.target.tagName === 'TEXTAREA') fitText(e.target);
        updateTotal();
      });
      rows.addEventListener('keydown', function (e) {
        // Enter inside the slip table never submits the form or breaks a line
        if (e.key === 'Enter' && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
          e.preventDefault();
          var inputs = $all('input:not([type=hidden]), textarea', rows);
          var i = inputs.indexOf(e.target);
          if (i > -1 && inputs[i + 1]) inputs[i + 1].focus();
        }
      });
    }

    // Earliest reminder date comes from the card's data-earliest
    var extra = $('[data-earliest]', reviewForm);
    var pickup = reviewForm.querySelector('[name="pickup_date"]');
    if (extra && pickup && extra.getAttribute('data-earliest')) {
      pickup.setAttribute('min', extra.getAttribute('data-earliest'));
    }
    if (pickup && document.getElementById('pickup-hint')) pickup.setAttribute('aria-describedby', 'pickup-hint');
    // Validation message in the app's own date style, never the browser's mm/dd order
    if (extra && pickup) {
      var earliestLabel = extra.getAttribute('data-earliest-label');
      var checkPickup = function () {
        var min = pickup.getAttribute('min');
        var tooEarly = min && pickup.value && pickup.value < min;
        pickup.setCustomValidity(tooEarly && earliestLabel ? 'Earliest is ' + earliestLabel + '.' : (pickup.validity.badInput ? 'Pick a date.' : ''));
      };
      pickup.addEventListener('input', checkPickup);
      pickup.addEventListener('change', checkPickup);
      checkPickup();
    }

    // Slip date written as on the paper (dd/mm/yyyy); a hidden field carries the ISO value
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    var dmyToIso = function (v) {
      var m = /^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{2}|\d{4})$/.exec(v);
      if (!m) return '';
      var d = +m[1], mo = +m[2], y = +m[3];
      if (y < 100) y += 2000;
      var dt = new Date(y, mo - 1, d);
      if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) return '';
      return y + '-' + pad(mo) + '-' + pad(d);
    };
    var isoToDmy = function (v) {
      var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || '');
      return m ? m[3] + '/' + m[2] + '/' + m[1] : (v || '');
    };
    var dateWrap = $('[data-slip-date]', reviewForm);
    var nativeDate = dateWrap ? $('input', dateWrap) : null;
    if (nativeDate && nativeDate.type === 'date') {
      var textDate = document.createElement('input');
      textDate.type = 'text';
      textDate.setAttribute('inputmode', 'numeric');
      textDate.setAttribute('autocomplete', 'off');
      textDate.placeholder = 'dd/mm/yyyy';
      textDate.className = nativeDate.className;
      if (nativeDate.required) textDate.required = true;
      if (nativeDate.getAttribute('aria-invalid')) textDate.setAttribute('aria-invalid', nativeDate.getAttribute('aria-invalid'));
      textDate.value = isoToDmy(nativeDate.getAttribute('value'));
      if (nativeDate.id) { textDate.id = nativeDate.id; nativeDate.removeAttribute('id'); }
      nativeDate.type = 'hidden';
      dateWrap.insertBefore(textDate, nativeDate);
      var syncDate = function () {
        var v = textDate.value.trim();
        var iso = v ? dmyToIso(v) : '';
        nativeDate.value = iso || v;
        textDate.setCustomValidity(!v || iso ? '' : 'Write the date as dd/mm/yyyy, for example 17/11/2026.');
      };
      textDate.addEventListener('input', syncDate);
      textDate.addEventListener('blur', function () {
        var iso = dmyToIso(textDate.value.trim());
        if (iso) textDate.value = isoToDmy(iso);
      });
      syncDate();
    }

    // Read-only email preview follows the Roll No.
    var domain = reviewForm.getAttribute('data-email-domain') || '';
    var roll = reviewForm.querySelector('[name="enrollment_no"]');
    var emailSet = $('[data-email-set]', reviewForm);
    var emailUnset = $('[data-email-unset]', reviewForm);
    var emailAddr = $('[data-email-addr]', reviewForm);
    if (roll && emailSet && emailUnset && emailAddr) {
      var syncEmail = function () {
        var v = roll.value.replace(/\s+/g, '');
        emailAddr.textContent = v ? (v + '@' + domain).toLowerCase() : '';
        emailSet.hidden = !v;
        emailUnset.hidden = !!v;
      };
      roll.addEventListener('input', syncEmail);
      roll.addEventListener('change', syncEmail);
      syncEmail();
    }

    // Slip photo from this tab's own copy
    var media = $('[data-photo-key]');
    var photoKey = media ? (media.getAttribute('data-photo-key') || '') : '';
    var readPhoto = function () {
      if (!photoKey) return '';
      var v = storageGet(PHOTO_PREFIX + photoKey);
      return v && v.indexOf('data:image/') === 0 ? v : '';
    };
    if (media) {
      var present = $('[data-photo-present]', media);
      var split = $('[data-split]');
      var showPhoto = function (on) {
        if (present) present.hidden = !on;
        if (split) split.classList.toggle('has-photo', on);
      };
      var img = $('[data-photo-img]', media);
      var zoomBtn = $('[data-photo-zoom]', media);
      var stored = readPhoto();
      if (stored && img && present) {
        img.onerror = function () { showPhoto(false); };
        img.src = stored;
        if (zoomBtn) zoomBtn.setAttribute('data-zoom', stored);
        showPhoto(true);
      } else {
        showPhoto(false);
      }
    }
    var photoData = reviewForm.querySelector('[name="photo_data"]');
    reviewForm.addEventListener('submit', function () {
      if (photoData) photoData.value = readPhoto();
    });

    updateTotal();
    fitAll();
    window.addEventListener('resize', fitAll);
  }

  /* Detail: the slip is confirmed, drop the tab's photo copy */
  if ($('[data-slip-confirmed]')) clearStoredPhotos();

  /* Auth forms: focus the first field, or the first one with an error */
  $all('form[data-focus-first]').forEach(function (f) {
    var target = null;
    $all('.errorlist', f).forEach(function (list) {
      var box = list.closest('.field, .fieldset');
      var ctrl = box ? box.querySelector('input:not([type=hidden]), select, textarea') : null;
      if (!ctrl) return;
      if (ctrl.type !== 'checkbox') ctrl.setAttribute('aria-invalid', 'true');
      if (!target) target = ctrl;
    });
    if (!target) target = f.querySelector('input:not([type=hidden]):not([type=checkbox]), select, textarea');
    $all('[autofocus]', f).forEach(function (el) { el.removeAttribute('autofocus'); });
    if (target) target.focus();
  });

  /* Weekday chips: class fallback for browsers without :has() */
  $all('.check input[type="checkbox"]').forEach(function (cb) {
    var label = cb.closest('.check');
    var sync = function () { label.classList.toggle('is-checked', cb.checked); };
    cb.addEventListener('change', sync);
    sync();
  });

  /* Collect: steppers and live summary */
  var collectForm = $('[data-collect-form]');
  if (collectForm) {
    var sumRet = $('[data-sum-returned]', collectForm);
    var sumGiven = $('[data-sum-given]', collectForm);
    var sumMissing = $('[data-sum-missing]', collectForm);
    var summary = $('[data-summary]', collectForm);
    var missingWrap = $('[data-missing-wrap]', collectForm);

    var recompute = function () {
      var given = 0, ret = 0, missing = 0;
      $all('[data-check-row]', collectForm).forEach(function (row) {
        var g = toInt(row.getAttribute('data-given'));
        var input = $('[data-returned]', row);
        var r = toInt(input.value);
        given += g;
        ret += r;
        var short = Math.max(g - r, 0);
        missing += short;
        row.classList.toggle('is-short', short > 0);
      });
      sumRet.textContent = ret;
      sumGiven.textContent = given;
      sumMissing.textContent = missing;
      summary.classList.toggle('is-short', missing > 0);
      if (missingWrap) missingWrap.hidden = missing === 0;
    };

    collectForm.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-step]');
      if (!btn) return;
      var input = $('[data-returned]', btn.closest('[data-check-row]'));
      var next = toInt(input.value) + parseInt(btn.getAttribute('data-step'), 10);
      input.value = Math.max(next, 0);
      recompute();
    });
    collectForm.addEventListener('input', recompute);
    recompute();
  }
})();
