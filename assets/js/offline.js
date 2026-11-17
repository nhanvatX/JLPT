\
(function () {
  function getPrefix() {
    var s = document.currentScript || document.querySelector('script[data-root-prefix]');
    if (s && s.getAttribute('data-root-prefix') !== null) {
      return s.getAttribute('data-root-prefix');
    }
    var scripts = document.getElementsByTagName('script');
    for (var i = 0; i < scripts.length; i++) {
      var p = scripts[i].getAttribute('data-root-prefix');
      if (p !== null) return p;
    }
    return '';
  }
  var rootPrefix = getPrefix();

  // Disable offline-excluded links cleanly
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[data-offline-disabled="1"]') : null;
    if (a) {
      e.preventDefault();
      alert('Liên kết này nằm ngoài phạm vi offline JLPT N3-N2-N1 hoặc trỏ ra trang ngoài.');
    }
  });

  // Instant Offline Search
  function initSearch() {
    var input = document.getElementById('offline-search-input');
    var box = document.getElementById('offline-search-results');
    if (!input || !box || !window.OFFLINE_SEARCH_INDEX) return;

    function renderResults(q) {
      q = q.trim().toLowerCase();
      if (q.length < 1) {
        box.innerHTML = '';
        box.classList.remove('open');
        return;
      }
      var terms = q.split(/\s+/).filter(Boolean);
      var matches = [];
      var idx = window.OFFLINE_SEARCH_INDEX;
      for (var i = 0; i < idx.length; i++) {
        var item = idx[i];
        var hay = (item.t + ' ' + item.c).toLowerCase();
        var ok = true;
        for (var j = 0; j < terms.length; j++) {
          if (hay.indexOf(terms[j]) === -1) {
            ok = false;
            break;
          }
        }
        if (ok) {
          matches.push(item);
          if (matches.length >= 40) break;
        }
      }
      if (!matches.length) {
        box.innerHTML = '<div class="offline-search-empty">Không tìm thấy bài phù hợp trong dữ liệu offline N3-N2-N1.</div>';
      } else {
        var html = '';
        for (var k = 0; k < matches.length; k++) {
          var m = matches[k];
          html += '<a class="offline-search-item" href="' + rootPrefix + m.p + '">'
            + '<span class="offline-search-tag">' + escapeHtml(m.c) + '</span>'
            + escapeHtml(m.t) + '</a>';
        }
        box.innerHTML = html;
      }
      box.classList.add('open');
    }

    input.addEventListener('input', function () {
      renderResults(input.value);
    });
    input.addEventListener('focus', function () {
      if (input.value.trim().length > 0) renderResults(input.value);
    });
    document.addEventListener('click', function (e) {
      if (!e.target.closest('#topsearch')) {
        box.classList.remove('open');
      }
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Interactive Quiz Grader
  function initQuiz() {
    var entry = document.querySelector('.entry');
    if (!entry) return;
    var radios = entry.querySelectorAll('input[type="radio"][name^="quest"]');
    if (!radios.length) return;

    // Parse Answer Key
    var paragraphs = Array.prototype.slice.call(entry.querySelectorAll('p'));
    var answerKeyHeaderIdx = -1;
    for (var i = 0; i < paragraphs.length; i++) {
      if (/Answer\s*Key\s*:/i.test(paragraphs[i].textContent)) {
        answerKeyHeaderIdx = i;
        break;
      }
    }
    var answers = {};
    var answerNodes = [];
    if (answerKeyHeaderIdx !== -1) {
      for (var j = answerKeyHeaderIdx; j < paragraphs.length; j++) {
        var p = paragraphs[j];
        var txt = p.textContent || '';
        if (j > answerKeyHeaderIdx && /New words:|Grammar points in this test:|Kanji Lesson/i.test(txt)) {
          break;
        }
        answerNodes.push(p);
        var re = /Question\s*(\d+)\s*:\s*(\d+)/gi;
        var m;
        while ((m = re.exec(txt)) !== null) {
          answers[m[1]] = m[2];
        }
        // Stop right after we collected answer paragraph(s) before an <hr>
        var nextEl = p.nextElementSibling;
        if (j > answerKeyHeaderIdx && nextEl && nextEl.tagName === 'HR') {
          break;
        }
      }
    }

    // Group radio buttons by question number
    var qNames = [];
    var qSeen = {};
    for (var r = 0; r < radios.length; r++) {
      var name = radios[r].getAttribute('name');
      if (!qSeen[name]) {
        qSeen[name] = true;
        qNames.push(name);
      }
    }
    if (!Object.keys(answers).length) return;

    // Hide answer key by default so user can practice first
    var answerWrapper = document.createElement('div');
    answerWrapper.className = 'offline-answer-hidden';
    if (answerNodes.length) {
      answerNodes[0].parentNode.insertBefore(answerWrapper, answerNodes[0]);
      for (var a = 0; a < answerNodes.length; a++) {
        answerWrapper.appendChild(answerNodes[a]);
      }
    }

    // Insert control bar
    var bar = document.createElement('div');
    bar.className = 'offline-quiz-bar';
    bar.innerHTML = ''
      + '<button type="button" class="offline-btn" id="offline-grade-btn">Chấm điểm / Kiểm tra</button>'
      + '<button type="button" class="offline-btn offline-btn-secondary" id="offline-toggle-ans-btn">Hiện đáp án chi tiết</button>'
      + '<button type="button" class="offline-btn offline-btn-secondary" id="offline-reset-btn">Làm lại</button>'
      + '<span class="offline-score-box" id="offline-score-box">Số câu: ' + qNames.length + '</span>';
    entry.insertBefore(bar, entry.firstChild);

    var gradeBtn = document.getElementById('offline-grade-btn');
    var toggleBtn = document.getElementById('offline-toggle-ans-btn');
    var resetBtn = document.getElementById('offline-reset-btn');
    var scoreBox = document.getElementById('offline-score-box');

    toggleBtn.addEventListener('click', function () {
      var hidden = answerWrapper.classList.toggle('offline-answer-hidden');
      toggleBtn.textContent = hidden ? 'Hiện đáp án chi tiết' : 'Ẩn đáp án chi tiết';
    });

    gradeBtn.addEventListener('click', function () {
      var correct = 0;
      var total = 0;
      for (var q = 0; q < qNames.length; q++) {
        var name = qNames[q];
        var numMatch = name.match(/\d+/);
        if (!numMatch) continue;
        var qNum = numMatch[0];
        var ans = answers[qNum];
        if (!ans) continue;
        total++;

        var groupRadios = entry.querySelectorAll('input[type="radio"][name="' + name + '"]');
        var container = groupRadios[0].closest('p') || groupRadios[0].parentNode;
        container.classList.remove('offline-q-correct', 'offline-q-wrong');
        var oldFb = container.querySelector('.offline-q-feedback');
        if (oldFb) oldFb.remove();

        var checked = entry.querySelector('input[type="radio"][name="' + name + '"]:checked');
        var fb = document.createElement('span');
        fb.className = 'offline-q-feedback';
        if (checked && checked.value === ans) {
          correct++;
          container.classList.add('offline-q-correct');
          fb.style.color = '#1e7e34';
          fb.textContent = '✓ Đúng (Đáp án: ' + ans + ')';
        } else {
          container.classList.add('offline-q-wrong');
          fb.style.color = '#bd2130';
          fb.textContent = (checked ? '✗ Sai — ' : '• Chưa chọn — ') + 'Đáp án đúng: ' + ans;
        }
        container.appendChild(document.createElement('br'));
        container.appendChild(fb);
      }
      scoreBox.textContent = 'Kết quả: ' + correct + ' / ' + total + ' (' + Math.round((correct / Math.max(total, 1)) * 100) + '%)';
      answerWrapper.classList.remove('offline-answer-hidden');
      toggleBtn.textContent = 'Ẩn đáp án chi tiết';
    });

    resetBtn.addEventListener('click', function () {
      for (var r = 0; r < radios.length; r++) radios[r].checked = false;
      var containers = entry.querySelectorAll('.offline-q-correct, .offline-q-wrong');
      for (var c = 0; c < containers.length; c++) {
        containers[c].classList.remove('offline-q-correct', 'offline-q-wrong');
      }
      var fbs = entry.querySelectorAll('.offline-q-feedback');
      for (var f = 0; f < fbs.length; f++) fbs[f].remove();
      answerWrapper.classList.add('offline-answer-hidden');
      toggleBtn.textContent = 'Hiện đáp án chi tiết';
      scoreBox.textContent = 'Số câu: ' + qNames.length;
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initSearch();
    initQuiz();
  });
})();
