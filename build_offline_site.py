#!/usr/bin/env python3
"""
Build an offline HTML+CSS mirror of https://japanesetest4you.com/ for JLPT N3, N2, and N1.
Downloads all N3/N2/N1 tests, lists, category archives, kanji infographics, and grammar/vocab
flashcard pages, strips external requests (images, audio, embeds, trackers), and rewrites
every internal link to relative file://-compatible ASCII paths.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import posixpath
import re
import shutil
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_DIR = Path(__file__).resolve().parent
SITE_ORIGIN = "https://japanesetest4you.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CSS_FILES = [
    ("https://japanesetest4you.com/wp-content/themes/Destina/lib/css/reset.css", "assets/css/reset.css"),
    ("https://japanesetest4you.com/wp-content/themes/Destina/lib/css/defaults.css", "assets/css/defaults.css"),
    ("https://japanesetest4you.com/wp-content/themes/Destina/lib/css/ie.css", "assets/css/ie.css"),
    ("https://japanesetest4you.com/wp-content/themes/Destina/style.css", "assets/css/style.css"),
    ("https://japanesetest4you.com/wp-includes/css/dist/block-library/style.min.css", "assets/css/block-library.min.css"),
    ("https://japanesetest4you.com/wp-content/plugins/wp-post-navigation/style.css", "assets/css/wp-post-navigation.css"),
    ("https://japanesetest4you.com/wp-content/themes/Destina/pagenavi-css.css", "assets/css/pagenavi-css.css"),
]

LIST_SLUGS = [
    "jlpt-n1-grammar-list",
    "jlpt-n2-grammar-list",
    "jlpt-n3-grammar-list",
    "jlpt-n1-vocabulary-list",
    "jlpt-n2-vocabulary-list",
    "jlpt-n3-vocabulary-list",
    "jlpt-n3-kanji-list",
]

EXTRA_MAIN_PAGES = [
    ("https://japanesetest4you.com/how-to-self-study-japanese-effectively/", "pages/how-to-self-study-japanese-effectively.html"),
    ("https://japanesetest4you.com/japanese-verb-conjugation/", "pages/japanese-verb-conjugation.html"),
]

CATEGORY_SEEDS = [
    "https://japanesetest4you.com/category/jlpt-n1/",
    "https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-grammar-test/",
    "https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-kanji-test/",
    "https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-listening-test/",
    "https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-reading-test/",
    "https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-vocabulary-test/",
    "https://japanesetest4you.com/category/jlpt-n2/",
    "https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-grammar-test/",
    "https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-kanji-test/",
    "https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-listening-test/",
    "https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-reading-test/",
    "https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-vocabulary-test/",
    "https://japanesetest4you.com/category/jlpt-n3/",
    "https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-grammar-test/",
    "https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-kanji-test/",
    "https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-listening-test/",
    "https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-reading-test/",
    "https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-vocabulary-test/",
    "https://japanesetest4you.com/category/extra-exercise/",
    "https://japanesetest4you.com/category/extra-exercise/listening-comprehension-advanced/",
    "https://japanesetest4you.com/category/extra-exercise/listening-practice-intermediate/",
]

# Main site WP REST API categories (N1, N2, N3 tests, extra exercises, Kanji/Vocab infographics)
MAIN_API_CAT_GROUPS: List[Tuple[int, str, str]] = [
    (7, "n1", "JLPT N1 grammar test"),
    (8, "n1", "JLPT N1 Kanji test"),
    (6, "n1", "JLPT N1 listening test"),
    (50, "n1", "JLPT N1 reading test"),
    (9, "n1", "JLPT N1 vocabulary test"),
    (13, "n2", "JLPT N2 grammar test"),
    (10, "n2", "JLPT N2 Kanji test"),
    (19, "n2", "JLPT N2 listening test"),
    (36, "n2", "JLPT N2 reading test"),
    (16, "n2", "JLPT N2 vocabulary test"),
    (14, "n3", "JLPT N3 grammar test"),
    (11, "n3", "JLPT N3 Kanji test"),
    (20, "n3", "JLPT N3 listening test"),
    (29, "n3", "JLPT N3 reading test"),
    (17, "n3", "JLPT N3 vocabulary test"),
    (71, "extra", "Listening & Reading - Advanced"),
    (72, "extra", "Listening & Reading - Intermediate"),
    (61, "kanji", "Kanji and Vocabulary"),
]

# Flashcard site WP REST API categories (N1-N3 grammar/vocab + shared/N4-N5 grammar linked in N3-N1)
FC_API_CAT_GROUPS: List[Tuple[int, str, str]] = [
    (29, "grammar", "Learn Japanese N1 Grammar"),
    (22, "grammar", "Learn JLPT N2 Grammar"),
    (27, "grammar", "Learn Japanese N3 Grammar"),
    (10, "grammar", "Learn Japanese Grammar"),
    (15, "grammar", "Learn JLPT N4 Grammar"),
    (14, "grammar", "Learn JLPT N5 Grammar"),
    (20, "vocab", "Learn Japanese N1 Vocabulary"),
    (19, "vocab", "Learn Japanese N2 Vocabulary"),
    (18, "vocab", "Learn Japanese N3 Vocabulary"),
    (2, "vocab", "Learn Japanese Vocabulary"),
]

STATIC_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".mp3", ".wav", ".ogg", ".m4a", ".mp4", ".zip", ".rar",
    ".css", ".js", ".xml", ".txt",
}

EXCLUDE_SUBSTRINGS = [
    "/wp-admin", "/wp-login", "/wp-json", "xmlrpc.php", "/feed",
    "/wp-content/", "/wp-includes/", "/wp-comments-post.php",
    "/category/", "/ebook-", "/ebooks", "/support-jtest4you",
    "/privacy-policies", "/about-us", "/frequently-asked-questions",
    "/flashcard/contact", "flashcard-ebook-", "/author/", "/tag/", "/page/",
]


def make_session() -> requests.Session:
    """Create a thread-safe requests session with connection pooling and retries."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def canonicalize_url(raw_url: str) -> Optional[str]:
    """Normalize a japanesetest4you.com URL to canonical lowercase UTF-8 percent-encoded form."""
    if not raw_url:
        return None
    u_str = html_lib.unescape(raw_url.strip())
    if not u_str or u_str.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
        return None
    if u_str.startswith("//"):
        u_str = "https:" + u_str
    elif u_str.startswith("/"):
        u_str = SITE_ORIGIN + u_str

    parsed = urllib.parse.urlsplit(u_str)
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.netloc.lower()
    if host not in ("japanesetest4you.com", "www.japanesetest4you.com"):
        return None

    path = urllib.parse.unquote(parsed.path)
    path = re.sub(r"/{2,}", "/", path)
    if not path.startswith("/"):
        path = "/" + path
    encoded_path = urllib.parse.quote(path.encode("utf-8"), safe="/")
    encoded_path = encoded_path.rstrip("/") or "/"
    base = f"{SITE_ORIGIN}{encoded_path.lower()}"

    if parsed.query:
        m = re.fullmatch(r"p=(\d+)", parsed.query.strip(), re.I)
        if m:
            return f"{base}?p={m.group(1)}"
    return base


def split_url_and_fragment(raw_url: str, base_url: str) -> Tuple[Optional[str], str]:
    """Resolve raw_url against base_url and return (canonical_url, fragment)."""
    u_str = html_lib.unescape(raw_url.strip())
    if not u_str or u_str.startswith(("mailto:", "javascript:", "tel:", "data:")):
        return None, ""
    if u_str.startswith("#"):
        return None, u_str
    joined = urllib.parse.urljoin(base_url, u_str)
    parsed = urllib.parse.urlsplit(joined)
    frag = f"#{parsed.fragment}" if parsed.fragment else ""
    canon = canonicalize_url(joined)
    return canon, frag


def slug_to_ascii(slug: str, uid: str) -> str:
    """Convert any percent-encoded or Unicode slug into a short, safe ASCII filename."""
    decoded = urllib.parse.unquote(slug)
    ascii_only = re.sub(r"[^a-zA-Z0-9-]+", "-", decoded).strip("-").lower()
    ascii_only = re.sub(r"-{2,}", "-", ascii_only)
    if not ascii_only:
        ascii_only = "lesson"
    ascii_only = ascii_only[:80].rstrip("-")
    return f"{ascii_only}-{uid}.html"


def url_hash(canon_url: str) -> str:
    return hashlib.md5(canon_url.encode("utf-8")).hexdigest()[:8]


class PathRegistry:
    """Maintains collision-free mappings between canonical URLs and relative local HTML paths."""

    def __init__(self) -> None:
        self.url_to_rel: Dict[str, str] = {}
        self.used_rels: Set[str] = set()

    def register(self, url: str, preferred_rel: str, post_id: Optional[int] = None, is_flashcard: bool = False) -> str:
        canon = canonicalize_url(url)
        if not canon:
            raise ValueError(f"Invalid URL for registration: {url}")
        if canon in self.url_to_rel:
            rel = self.url_to_rel[canon]
        else:
            rel = preferred_rel.lstrip("/")
            if rel in self.used_rels:
                stem, ext = os.path.splitext(rel)
                rel = f"{stem}-{url_hash(canon)}{ext}"
            self.used_rels.add(rel)
            self.url_to_rel[canon] = rel

        if post_id is not None:
            short_base = f"{SITE_ORIGIN}/flashcard" if is_flashcard else SITE_ORIGIN
            short_url = f"{short_base}?p={post_id}"
            self.url_to_rel.setdefault(short_url, rel)
        return rel

    def lookup(self, canon_url: Optional[str]) -> Optional[str]:
        if not canon_url:
            return None
        return self.url_to_rel.get(canon_url)


def clean_text_title(raw_html_title: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", raw_html_title)).strip()


def format_date(iso_date: str) -> str:
    if not iso_date:
        return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(iso_date[:19])
        return dt.strftime("%B %d, %Y").replace(" 0", " ")
    except Exception:
        return iso_date[:10]


def fetch_text(session: requests.Session, url: str, timeout: int = 20) -> Optional[str]:
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text
    except Exception as exc:
        print(f"  [WARN] Failed to fetch {url}: {exc}")
    return None


def fetch_binary(session: requests.Session, url: str, timeout: int = 20) -> Optional[bytes]:
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def download_assets(session: requests.Session) -> None:
    """Phase A: Download CSS files, strip external imports, download theme icons/logo."""
    print("[Phase A] Downloading CSS & theme UI assets...")
    css_dir = BASE_DIR / "assets" / "css"
    css_img_dir = css_dir / "images"
    img_dir = BASE_DIR / "assets" / "images"
    js_dir = BASE_DIR / "assets" / "js"
    for d in (css_dir, css_img_dir, img_dir, js_dir):
        d.mkdir(parents=True, exist_ok=True)

    style_urls_found: Set[str] = set()
    for url, rel in CSS_FILES:
        content = fetch_text(session, url)
        if content is None:
            print(f"  [WARN] Missing CSS: {url}")
            content = ""
        if rel.endswith("style.css"):
            content = re.sub(r"@import\s+url\([^)]*https?://[^)]*\)\s*;?", "", content, flags=re.I)
            for m in re.findall(r"url\(\s*['\"]?(images/[^)'\"]+)['\"]?\s*\)", content):
                style_urls_found.add(m)
        (BASE_DIR / rel).write_text(content, encoding="utf-8")

    # Download theme images referenced by style.css; drop broken url() references if 404
    missing_css_imgs: Set[str] = set()
    for img_rel in sorted(style_urls_found):
        img_url = f"{SITE_ORIGIN}/wp-content/themes/Destina/{img_rel}"
        data = fetch_binary(session, img_url)
        if data:
            out_path = css_dir / img_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(data)
        else:
            missing_css_imgs.add(img_rel)

    if missing_css_imgs:
        style_path = css_dir / "style.css"
        style_text = style_path.read_text(encoding="utf-8")
        for m_rel in missing_css_imgs:
            style_text = re.sub(
                rf"background\s*:\s*url\(\s*['\"]?{re.escape(m_rel)}['\"]?\s*\)[^;]*;",
                "",
                style_text,
            )
            style_text = re.sub(
                rf"url\(\s*['\"]?{re.escape(m_rel)}['\"]?\s*\)",
                "none",
                style_text,
            )
        style_path.write_text(style_text, encoding="utf-8")

    # Download logo and favicon
    logo_data = fetch_binary(session, f"{SITE_ORIGIN}/wp-content/uploads/2018/08/logo-jt4u-2.png")
    if logo_data:
        (img_dir / "logo.png").write_bytes(logo_data)
    fav_data = fetch_binary(session, f"{SITE_ORIGIN}/images/favicon/favicon.png")
    if fav_data:
        (img_dir / "favicon.png").write_bytes(fav_data)

    write_custom_css_and_js(css_dir, js_dir)
    print(f"  Downloaded {len(CSS_FILES)} CSS files and {len(style_urls_found) - len(missing_css_imgs)} theme images.")


def write_custom_css_and_js(css_dir: Path, js_dir: Path) -> None:
    """Create local offline enhancements CSS and offline interactive JS."""
    custom_css = """\
/* Offline mirror enhancements for JLPT N3-N2-N1 */
.auto-style1 {
    text-decoration: underline;
}
.has-vivid-red-color {
    color: #cf2e2e;
}
.menu-primary li {
    float: left;
    position: relative;
    list-style: none;
}
.menu-primary li ul {
    display: none;
    position: absolute;
    top: 100%;
    left: 0;
    background: #fff;
    border: 1px solid #ccc;
    box-shadow: 0 3px 8px rgba(0,0,0,0.18);
    z-index: 999;
    min-width: 210px;
    padding: 4px 0;
}
.menu-primary li:hover > ul {
    display: block;
}
.menu-primary li ul li {
    float: none;
    width: 100%;
}
.menu-primary li ul li a {
    display: block;
    padding: 8px 14px;
    font-size: 13px;
    text-transform: none;
    color: #222;
    text-shadow: none;
    background: #fff;
    border-bottom: 1px solid #f0f0f0;
}
.menu-primary li ul li a:hover {
    background: #FFAE00;
    color: #fff;
}
#topsearch {
    position: relative;
}
.offline-search-results {
    display: none;
    position: absolute;
    top: 34px;
    right: 0;
    width: 390px;
    max-height: 420px;
    overflow-y: auto;
    background: #fff;
    border: 1px solid #ccc;
    box-shadow: 0 4px 14px rgba(0,0,0,0.22);
    z-index: 9999;
    text-align: left;
}
.offline-search-results.open {
    display: block;
}
.offline-search-item {
    display: block;
    padding: 8px 12px;
    border-bottom: 1px solid #eee;
    color: #222 !important;
    font-size: 13px;
    line-height: 1.35;
}
.offline-search-item:hover {
    background: #fff7e6;
}
.offline-search-tag {
    display: inline-block;
    font-size: 11px;
    font-weight: bold;
    padding: 1px 6px;
    border-radius: 3px;
    background: #f0ad4e;
    color: #fff;
    margin-right: 6px;
}
.offline-search-empty {
    padding: 10px 12px;
    color: #666;
    font-size: 13px;
}
.offline-banner {
    background: #fff8e7;
    border: 1px solid #f0c36d;
    border-left: 4px solid #e47c03;
    padding: 8px 12px;
    margin-bottom: 14px;
    font-size: 12.5px;
    color: #444;
    border-radius: 3px;
}
.offline-dashboard {
    background: #fafafa;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 14px;
    margin-bottom: 18px;
}
.offline-dashboard h2 {
    margin: 0 0 10px 0;
    font-size: 17px;
    color: #c96200;
    border-bottom: 2px solid #FFAE00;
    padding-bottom: 5px;
}
.offline-level-grid {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}
.offline-level-card {
    flex: 1 1 175px;
    background: #fff;
    border: 1px solid #e0e0e0;
    border-top: 3px solid #E47C03;
    padding: 10px;
    border-radius: 3px;
}
.offline-level-card h3 {
    margin: 0 0 8px 0;
    font-size: 15px;
    color: #222;
}
.offline-level-card ul {
    margin: 0;
    padding-left: 16px;
    font-size: 13px;
    line-height: 1.6;
}
.offline-media-note {
    display: inline-block;
    background: #f4f4f4;
    border: 1px dashed #bbb;
    color: #666;
    font-size: 12px;
    padding: 4px 10px;
    margin: 4px 0;
    border-radius: 3px;
}
.offline-quiz-bar {
    position: sticky;
    top: 0;
    z-index: 50;
    background: #fffbe8;
    border: 1px solid #e6c667;
    padding: 10px 12px;
    margin: 10px 0 16px 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    border-radius: 4px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.06);
}
.offline-btn {
    background: #E47C03;
    color: #fff;
    border: none;
    padding: 6px 12px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 13px;
    font-weight: bold;
}
.offline-btn:hover {
    background: #c96800;
}
.offline-btn-secondary {
    background: #555;
}
.offline-btn-secondary:hover {
    background: #333;
}
.offline-score-box {
    font-weight: bold;
    font-size: 13.5px;
    color: #222;
    margin-left: auto;
}
.offline-q-correct {
    background: #e9f7ef !important;
    border-left: 4px solid #28a745;
    padding: 6px 10px;
    border-radius: 3px;
}
.offline-q-wrong {
    background: #fdecea !important;
    border-left: 4px solid #dc3545;
    padding: 6px 10px;
    border-radius: 3px;
}
.offline-q-feedback {
    display: inline-block;
    margin-top: 4px;
    font-size: 12.5px;
    font-weight: bold;
}
.offline-answer-hidden {
    display: none;
}
a[data-offline-disabled="1"] {
    color: #999 !important;
    opacity: 0.78;
    cursor: not-allowed;
}
.offline-n2-vocab-cta {
    background: #fff6e0;
    border: 1px solid #f0b840;
    padding: 10px 14px;
    margin: 10px 0 16px 0;
    border-radius: 4px;
    font-size: 14px;
}
.offline-list-columns {
    column-count: 2;
    column-gap: 20px;
    font-size: 13.5px;
    line-height: 1.75;
}
"""
    (css_dir / "offline-custom.css").write_text(custom_css, encoding="utf-8")

    offline_js = r"""\
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
"""
    (js_dir / "offline.js").write_text(offline_js, encoding="utf-8")


def extract_inner_content(raw_html: str) -> str:
    """Extract the inner HTML of <div id="content">...</div> up to <div id="sidebar-primary">."""
    start = raw_html.find('<div id="content">')
    end = raw_html.find('<div id="sidebar-primary">')
    if start == -1 or end == -1 or end <= start:
        return ""
    inner = raw_html[start + len('<div id="content">') : end]
    # Remove the single closing </div> that closes <div id="content">
    last_div = inner.rfind("</div>")
    if last_div != -1:
        inner = inner[:last_div]
    return inner.strip()


def extract_page_title(raw_html: str, default: str = "Japanesetest4you.com Offline") -> str:
    m = re.search(r"<title>(.*?)</title>", raw_html, re.S | re.I)
    if m:
        return clean_text_title(m.group(1))
    return default


def extract_sidebar_and_footer(home_html: str) -> Tuple[str, str]:
    """Extract sidebar-primary and footer blocks from homepage HTML."""
    sb_start = home_html.find('<div id="sidebar-primary">')
    sb_end = home_html.find("</div><!-- #sidebar-primary -->")
    sidebar = ""
    if sb_start != -1 and sb_end != -1:
        sidebar = home_html[sb_start : sb_end + len("</div><!-- #sidebar-primary -->")]

    fw_start = home_html.find('<div id="footer-widgets"')
    ft_end = home_html.find("</div><!-- #footer -->")
    footer = ""
    if fw_start != -1 and ft_end != -1:
        footer = home_html[fw_start : ft_end + len("</div><!-- #footer -->")]

    return sidebar, footer


def build_offline_nav_menu() -> str:
    """Build primary navigation bar tailored for offline JLPT N3, N2, N1 browsing."""
    return """\
<div class="clearfix">
  <div class="menu-primary-container">
    <ul id="menu-menu-1" class="menus menu-primary">
      <li class="menu-item"><a href="https://japanesetest4you.com/">Home (N3-N2-N1)</a></li>
      <li class="menu-item"><a href="https://japanesetest4you.com/category/jlpt-n3/">JLPT N3</a>
        <ul>
          <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-grammar-test/">N3 Grammar Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-kanji-test/">N3 Kanji Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-vocabulary-test/">N3 Vocabulary Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-reading-test/">N3 Reading Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-listening-test/">N3 Listening Tests</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n3-grammar-list/">N3 Grammar List</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n3-vocabulary-list/">N3 Vocabulary List</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n3-kanji-list/">N3 Kanji List</a></li>
        </ul>
      </li>
      <li class="menu-item"><a href="https://japanesetest4you.com/category/jlpt-n2/">JLPT N2</a>
        <ul>
          <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-grammar-test/">N2 Grammar Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-kanji-test/">N2 Kanji Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-vocabulary-test/">N2 Vocabulary Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-reading-test/">N2 Reading Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-listening-test/">N2 Listening Tests</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n2-grammar-list/">N2 Grammar List</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n2-vocabulary-list/">N2 Vocabulary List</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n2-vocabulary-flashcard-list/">N2 Vocabulary Flashcards (740)</a></li>
        </ul>
      </li>
      <li class="menu-item"><a href="https://japanesetest4you.com/category/jlpt-n1/">JLPT N1</a>
        <ul>
          <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-grammar-test/">N1 Grammar Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-kanji-test/">N1 Kanji Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-vocabulary-test/">N1 Vocabulary Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-reading-test/">N1 Reading Tests</a></li>
          <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-listening-test/">N1 Listening Tests</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n1-grammar-list/">N1 Grammar List</a></li>
          <li><a href="https://japanesetest4you.com/jlpt-n1-vocabulary-list/">N1 Vocabulary List</a></li>
        </ul>
      </li>
      <li class="menu-item"><a href="https://japanesetest4you.com/flashcard/">Flashcards</a></li>
      <li class="menu-item"><a href="https://japanesetest4you.com/category/infographics/vocabulary-list/">Kanji Infographics</a></li>
    </ul>
  </div>
</div>"""


def build_offline_dashboard_html() -> str:
    """Dashboard inserted at the top of index.html for instant offline navigation."""
    return """\
<div class="offline-dashboard">
  <h2>JLPT N3 &ndash; N2 &ndash; N1 Offline Portal</h2>
  <div class="offline-level-grid">
    <div class="offline-level-card">
      <h3>JLPT N3 (Intermediate)</h3>
      <ul>
        <li><a href="https://japanesetest4you.com/jlpt-n3-grammar-list/">N3 Grammar List</a></li>
        <li><a href="https://japanesetest4you.com/jlpt-n3-vocabulary-list/">N3 Vocabulary List</a></li>
        <li><a href="https://japanesetest4you.com/jlpt-n3-kanji-list/">N3 Kanji List</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-grammar-test/">N3 Grammar Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-kanji-test/">N3 Kanji Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-vocabulary-test/">N3 Vocabulary Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-reading-test/">N3 Reading Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n3/jlpt-n3-listening-test/">N3 Listening Tests</a></li>
      </ul>
    </div>
    <div class="offline-level-card">
      <h3>JLPT N2 (Upper-Interm.)</h3>
      <ul>
        <li><a href="https://japanesetest4you.com/jlpt-n2-grammar-list/">N2 Grammar List</a></li>
        <li><a href="https://japanesetest4you.com/jlpt-n2-vocabulary-list/">N2 Vocabulary List</a></li>
        <li><a href="https://japanesetest4you.com/jlpt-n2-vocabulary-flashcard-list/">N2 Vocab Flashcards (740)</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-grammar-test/">N2 Grammar Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-kanji-test/">N2 Kanji Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-vocabulary-test/">N2 Vocabulary Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-reading-test/">N2 Reading Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n2/jlpt-n2-listening-test/">N2 Listening Tests</a></li>
      </ul>
    </div>
    <div class="offline-level-card">
      <h3>JLPT N1 (Advanced)</h3>
      <ul>
        <li><a href="https://japanesetest4you.com/jlpt-n1-grammar-list/">N1 Grammar List</a></li>
        <li><a href="https://japanesetest4you.com/jlpt-n1-vocabulary-list/">N1 Vocabulary List</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-grammar-test/">N1 Grammar Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-kanji-test/">N1 Kanji Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-vocabulary-test/">N1 Vocabulary Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-reading-test/">N1 Reading Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/jlpt-n1/jlpt-n1-listening-test/">N1 Listening Tests</a></li>
        <li><a href="https://japanesetest4you.com/category/infographics/vocabulary-list/">Kanji Infographics (521)</a></li>
      </ul>
    </div>
  </div>
</div>"""


def sanitize_body_html(body_html: str) -> str:
    """Remove all scripts, trackers, embeds, ads, audio, and external img src/srcset."""
    # Remove scripts, noscripts, style links
    body_html = re.sub(r"<script\b[^>]*>.*?</script>", "", body_html, flags=re.S | re.I)
    body_html = re.sub(r"<noscript\b[^>]*>.*?</noscript>", "", body_html, flags=re.S | re.I)
    body_html = re.sub(r"<link\b[^>]*>", "", body_html, flags=re.I)

    # Remove Facebook share widgets (bounded so we never cross closing </div>)
    body_html = re.sub(r'<div\s+id=["\']fb-root["\'][^>]*>\s*</div>', "", body_html, flags=re.S | re.I)
    body_html = re.sub(
        r'<div\s+class=["\']fb-share-button["\'][^>]*>(?:(?!</div>).)*?</div>',
        "",
        body_html,
        flags=re.S | re.I,
    )

    # Remove Patreon ad figures / images (never cross </figure> boundary)
    body_html = re.sub(
        r"<figure\b[^>]*>(?:(?!</figure>).)*?patreon\.com(?:(?!</figure>).)*?</figure>",
        "",
        body_html,
        flags=re.S | re.I,
    )
    body_html = re.sub(
        r"<div\b[^>]*class=[\"'][^\"']*wp-block-image[^\"']*[\"'][^>]*>\s*</div>",
        "",
        body_html,
        flags=re.S | re.I,
    )

    # Replace YouTube / embed iframes with offline placeholder
    def repl_iframe(m: re.Match) -> str:
        tag = m.group(0)
        tm = re.search(r'title=["\']([^"\']+)["\']', tag, re.I)
        title = html_lib.escape(html_lib.unescape(tm.group(1))) if tm else "Video"
        return f'<div class="offline-media-note">[Video YouTube: {title} &mdash; Đã ẩn ở chế độ offline]</div>'

    body_html = re.sub(r"<iframe\b[^>]*>.*?</iframe>", repl_iframe, body_html, flags=re.S | re.I)

    # Replace <audio>...</audio> blocks with offline placeholder
    body_html = re.sub(
        r"<audio\b[^>]*>.*?</audio>",
        '<div class="offline-media-note">[Audio MP3 &mdash; Đã lược bỏ ở chế độ HTML+CSS offline]</div>',
        body_html,
        flags=re.S | re.I,
    )
    body_html = re.sub(r"<source\b[^>]*>", "", body_html, flags=re.I)

    # Remove or replace external <img> tags (preserving only local logo marker if used)
    def repl_img(m: re.Match) -> str:
        tag = m.group(0)
        if "data-local-asset=" in tag:
            return tag
        low = tag.lower()
        if any(x in low for x in ("statcounter", "advertisement", "mainpage-ad")):
            return ""
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', tag, re.I)
        alt_txt = html_lib.unescape(alt_m.group(1)).strip() if alt_m else ""
        if alt_txt and alt_txt.lower() not in ("japanesetest4you.com", ""):
            return f'<span class="offline-media-note">[Ảnh: {html_lib.escape(alt_txt)}]</span>'
        return '<span class="offline-media-note">[Hình ảnh đã lược bỏ ở chế độ HTML+CSS offline]</span>'

    body_html = re.sub(r"<img\b[^>]*>", repl_img, body_html, flags=re.I)
    # Remove redundant "Click on image to view full size." captions left from stripped infographics
    body_html = re.sub(
        r'<p[^>]*class=["\']wp-caption-text["\'][^>]*>Click on image to view full size\.</p>',
        "",
        body_html,
        flags=re.I,
    )
    return body_html


def rewrite_links(html_text: str, current_rel: str, orig_url: str, registry: PathRegistry) -> str:
    """Rewrite all <a href="..."> in html_text to relative paths or offline-disabled placeholders."""
    current_dir = posixpath.dirname(current_rel)

    def repl_anchor(m: re.Match) -> str:
        full_attr = m.group(0)
        quote_char = m.group(1)
        raw_href = m.group(2)
        trimmed = raw_href.strip()

        if not trimmed or trimmed.startswith("#"):
            return full_attr
        if trimmed.lower().startswith(("mailto:", "javascript:", "tel:")):
            return f'href={quote_char}#offline-disabled{quote_char} data-offline-disabled="1" title="Liên kết ngoài bị vô hiệu hóa offline"'

        canon, frag = split_url_and_fragment(trimmed, orig_url)
        target_rel = registry.lookup(canon)
        if target_rel:
            rel_link = posixpath.relpath(target_rel, current_dir or ".")
            if frag == "#respond":
                frag = ""
            return f"href={quote_char}{rel_link}{frag}{quote_char}"

        return (
            f'href={quote_char}#offline-disabled{quote_char} '
            f'data-offline-disabled="1" '
            f'title="Ngoài phạm vi offline N3-N2-N1"'
        )

    # Remove target="_blank" so offline links open in the same tab cleanly
    html_text = re.sub(r'\s+target=["\']_blank["\']', "", html_text, flags=re.I)
    html_text = re.sub(r'href=(["\'])([^"\']*)\1', repl_anchor, html_text, flags=re.I)
    return html_text


def render_page(
    title: str,
    content_inner_html: str,
    current_rel: str,
    orig_url: str,
    sidebar_html: str,
    footer_html: str,
    registry: PathRegistry,
    body_class: str = "single single-post",
) -> str:
    """Wrap sanitized #content HTML inside the Destina shell and rewrite all links."""
    depth = current_rel.count("/")
    prefix = "../" * depth
    nav_html = build_offline_nav_menu()
    safe_title = html_lib.escape(clean_text_title(title))

    raw_shell = f"""<!DOCTYPE html>
<html lang="en-US">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{safe_title}</title>
<link rel="shortcut icon" href="{prefix}assets/images/favicon.png" type="image/x-icon" />
<link rel="stylesheet" href="{prefix}assets/css/reset.css" type="text/css" media="screen, projection" />
<link rel="stylesheet" href="{prefix}assets/css/defaults.css" type="text/css" media="screen, projection" />
<!--[if lt IE 8]><link rel="stylesheet" href="{prefix}assets/css/ie.css" type="text/css" media="screen, projection" /><![endif]-->
<link rel="stylesheet" href="{prefix}assets/css/style.css" type="text/css" media="screen, projection" />
<link rel="stylesheet" href="{prefix}assets/css/block-library.min.css" type="text/css" media="all" />
<link rel="stylesheet" href="{prefix}assets/css/wp-post-navigation.css" type="text/css" media="all" />
<link rel="stylesheet" href="{prefix}assets/css/pagenavi-css.css" type="text/css" media="all" />
<link rel="stylesheet" href="{prefix}assets/css/offline-custom.css" type="text/css" media="all" />
</head>
<body id="top" class="{body_class}">
<div id="container">
  <div id="header">
    <div class="logo">
      <a href="https://japanesetest4you.com/"><img data-local-asset="1" src="{prefix}assets/images/logo.png" alt="Japanesetest4you.com" title="Japanesetest4you.com" /></a>
    </div>
    <div class="header-right">
      <div id="topsearch">
        <div id="search" title="Tìm kiếm bài học offline">
          <form id="offline-search-form" onsubmit="return false;">
            <input type="text" id="offline-search-input" placeholder="Tìm bài N3 / N2 / N1..." autocomplete="off" />
          </form>
        </div>
        <div id="offline-search-results" class="offline-search-results"></div>
      </div>
      <div id="top-social-profiles"></div>
    </div>
  </div>
  {nav_html}
  <div id="main">
    <div id="content">
      <div class="offline-banner">
        Chế độ Offline (HTML + CSS) &bull; Phạm vi: <strong>JLPT N3 &ndash; N2 &ndash; N1</strong> &bull;
        <a href="https://japanesetest4you.com/">Trang chủ N3-N2-N1</a>
      </div>
      {content_inner_html}
    </div>
    {sidebar_html}
  </div>
  {footer_html}
</div>
<script data-root-prefix="{prefix}" src="{prefix}assets/js/search-index.js"></script>
<script data-root-prefix="{prefix}" src="{prefix}assets/js/offline.js"></script>
</body>
</html>
"""
    # Sanitize body portion only (preserve our <head> links and bottom local <script> tags)
    body_start = raw_shell.find("<body")
    head_part = raw_shell[:body_start]
    body_part = raw_shell[body_start:]

    script_marker = f'<script data-root-prefix="{prefix}"'
    s_idx = body_part.find(script_marker)
    body_main = body_part[:s_idx]
    body_scripts = body_part[s_idx:]

    body_main = sanitize_body_html(body_main)
    body_main = rewrite_links(body_main, current_rel, orig_url, registry)
    return head_part + body_main + body_scripts


def render_post_content(
    post: dict,
    cat_meta_links: List[Tuple[str, str]],
    prev_post: Optional[dict],
    next_post: Optional[dict],
) -> str:
    """Build <div class="post ..."> HTML from a WordPress REST API post object."""
    pid = post["id"]
    title = post["title"]["rendered"]
    date_str = format_date(post.get("date", ""))
    content = post["content"]["rendered"]

    cat_html = ", ".join(
        f'<a href="{html_lib.escape(u)}" rel="category tag">{html_lib.escape(name)}</a>'
        for name, u in cat_meta_links
    )
    prev_html = (
        f'<a href="{html_lib.escape(prev_post["link"])}">{clean_text_title(prev_post["title"]["rendered"])}</a>'
        if prev_post else ""
    )
    next_html = (
        f'<a href="{html_lib.escape(next_post["link"])}">{clean_text_title(next_post["title"]["rendered"])}</a>'
        if next_post else ""
    )
    nav_block = ""
    if prev_html or next_html:
        nav_block = f"""
<div style="font-size: 0px; height: 0px; line-height: 0px; margin: 0; padding: 0; clear: both;"></div>
<div class="wp-post-navigation">
  <div class="wp-post-navigation-pre">{prev_html}</div>
  <div class="wp-post-navigation-next">{next_html}</div>
</div>"""

    return f"""
<div class="post post-single clearfix post-{pid} type-post status-publish format-standard hentry" id="post-{pid}">
  <h2 class="title">{title}</h2>
  <div class="postmeta-primary">
    <span class="meta_date">{html_lib.escape(date_str)}</span>
    &nbsp; <span class="meta_categories">{cat_html}</span>
  </div>
  <div class="entry clearfix">
    {content}
    {nav_block}
  </div>
</div>"""


def fetch_wp_category_posts(
    session: requests.Session,
    api_base: str,
    cat_id: int,
) -> List[dict]:
    """Fetch all posts for a category from the WP REST API using concurrent pagination."""
    fields = "id,slug,link,date,title,content,categories"
    first_url = f"{api_base}/posts?categories={cat_id}&per_page=100&page=1&_fields={fields}"
    resp = session.get(first_url, timeout=25)
    resp.raise_for_status()
    posts = resp.json()
    total_pages = int(resp.headers.get("X-WP-TotalPages", "1"))
    if total_pages > 1:
        def get_page(p: int) -> List[dict]:
            u = f"{api_base}/posts?categories={cat_id}&per_page=100&page={p}&_fields={fields}"
            r = session.get(u, timeout=25)
            r.raise_for_status()
            return r.json()

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(get_page, p): p for p in range(2, total_pages + 1)}
            page_results: Dict[int, List[dict]] = {}
            for fut in as_completed(futs):
                p_num = futs[fut]
                page_results[p_num] = fut.result()
            for p_num in range(2, total_pages + 1):
                posts.extend(page_results[p_num])
    return posts


def should_gapfill_url(canon: str) -> bool:
    """Determine if a linked japanesetest4you.com URL should be gap-filled."""
    low = canon.lower()
    if any(low.endswith(ext) for ext in STATIC_EXTS):
        return False
    if any(sub in low for sub in EXCLUDE_SUBSTRINGS):
        return False
    if "/flashcard/" not in low and ("jlpt-n4-" in low or "jlpt-n5-" in low):
        return False
    if "?p=" in low:
        return False
    return True


def extract_candidate_links(html_chunk: str, base_url: str) -> Set[str]:
    out: Set[str] = set()
    for raw_href in re.findall(r'href=["\']([^"\']+)["\']', html_chunk, re.I):
        canon, _ = split_url_and_fragment(raw_href, base_url)
        if canon and should_gapfill_url(canon):
            out.add(canon)
    return out


def main() -> None:
    t_start = time.time()
    for folder in ("lists", "categories", "tests", "kanji", "flashcard", "pages"):
        target_dir = BASE_DIR / folder
        if target_dir.exists():
            shutil.rmtree(target_dir)
    session = make_session()
    registry = PathRegistry()

    # Phase A: CSS & UI assets
    download_assets(session)

    # Phase B: Direct HTML pages (Homepage, Flashcard Hub, 7 Lists, Extra pages, Categories + Pagination)
    print("[Phase B] Fetching hub, list, and category archive pages...")
    home_url = f"{SITE_ORIGIN}/"
    home_html = fetch_text(session, home_url)
    if not home_html:
        raise RuntimeError("Failed to fetch homepage!")
    sidebar_html, footer_html = extract_sidebar_and_footer(home_html)

    direct_pages: Dict[str, Tuple[str, str, str]] = {}  # canon -> (rel, title, inner_content)

    # Register and store Homepage + Flashcard Hub + List pages + Extra pages
    hub_targets: List[Tuple[str, str]] = [
        (home_url, "index.html"),
        (f"{SITE_ORIGIN}/flashcard/", "flashcard/index.html"),
    ]
    for slug in LIST_SLUGS:
        hub_targets.append((f"{SITE_ORIGIN}/{slug}/", f"lists/{slug}.html"))
    hub_targets.extend(EXTRA_MAIN_PAGES)

    with ThreadPoolExecutor(max_workers=12) as ex:
        fut_map = {ex.submit(fetch_text, session, u): (u, rel) for u, rel in hub_targets}
        for fut in as_completed(fut_map):
            u, rel = fut_map[fut]
            raw = fut.result()
            if raw:
                canon = canonicalize_url(u)
                registry.register(u, rel)
                title = extract_page_title(raw)
                inner = extract_inner_content(raw)
                if canon == canonicalize_url(home_url):
                    inner = build_offline_dashboard_html() + "\n" + inner
                direct_pages[canon] = (rel, title, inner)

    # Fetch category seed page 1s and discover pagination
    cat_page1_results: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=12) as ex:
        fut_map = {ex.submit(fetch_text, session, u): u for u in CATEGORY_SEEDS}
        for fut in as_completed(fut_map):
            u = fut_map[fut]
            raw = fut.result()
            if raw:
                cat_page1_results[u] = raw

    extra_cat_pages: List[Tuple[str, str]] = []
    for base_cat_url, raw in cat_page1_results.items():
        subpath = urllib.parse.urlsplit(base_cat_url).path.strip("/")
        # subpath starts with category/...
        cat_rel = f"categories/{subpath.replace('category/', '', 1)}.html"
        canon = canonicalize_url(base_cat_url)
        registry.register(base_cat_url, cat_rel)
        direct_pages[canon] = (cat_rel, extract_page_title(raw), extract_inner_content(raw))

        # Check pagination
        max_page = 1
        m_pages = re.search(r"Page\s+1\s+of\s+(\d+)", raw, re.I)
        if m_pages:
            max_page = int(m_pages.group(1))
        else:
            for pm in re.findall(r"/page/(\d+)/", raw):
                max_page = max(max_page, int(pm))
        for p_num in range(2, max_page + 1):
            p_url = f"{base_cat_url.rstrip('/')}/page/{p_num}/"
            p_rel = f"categories/{subpath.replace('category/', '', 1)}-page-{p_num}.html"
            extra_cat_pages.append((p_url, p_rel))

    print(f"  Fetching {len(extra_cat_pages)} paginated category archive pages...")
    with ThreadPoolExecutor(max_workers=14) as ex:
        fut_map = {ex.submit(fetch_text, session, u): (u, rel) for u, rel in extra_cat_pages}
        for fut in as_completed(fut_map):
            u, rel = fut_map[fut]
            raw = fut.result()
            if raw:
                canon = canonicalize_url(u)
                registry.register(u, rel)
                direct_pages[canon] = (rel, extract_page_title(raw), extract_inner_content(raw))

    # Phase C: Bulk fetch posts via WP REST API
    print("[Phase C] Bulk fetching N3-N2-N1 posts via WP REST API...")
    main_cats_json = json.loads(fetch_text(session, f"{SITE_ORIGIN}/wp-json/wp/v2/categories?per_page=100") or "[]")
    main_cat_info = {c["id"]: (c["name"], c["link"], c["slug"]) for c in main_cats_json}

    fc_cats_json = json.loads(fetch_text(session, f"{SITE_ORIGIN}/flashcard/wp-json/wp/v2/categories?per_page=100") or "[]")
    fc_cat_info = {c["id"]: (c["name"], c["link"], c["slug"]) for c in fc_cats_json}

    main_posts_by_id: Dict[int, dict] = {}
    main_cat_posts: Dict[int, List[dict]] = {}
    main_post_bucket: Dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(fetch_wp_category_posts, session, f"{SITE_ORIGIN}/wp-json/wp/v2", cid): (cid, bucket)
            for cid, bucket, _ in MAIN_API_CAT_GROUPS
        }
        for fut in as_completed(futs):
            cid, bucket = futs[fut]
            plist = fut.result()
            main_cat_posts[cid] = plist
            for p in plist:
                pid = p["id"]
                main_posts_by_id[pid] = p
                # Prefer n1/n2/n3 test bucket over kanji/extra if a post appears in multiple
                if pid not in main_post_bucket or bucket in ("n1", "n2", "n3"):
                    main_post_bucket[pid] = bucket

    fc_posts_by_id: Dict[int, dict] = {}
    fc_cat_posts: Dict[int, List[dict]] = {}
    fc_post_bucket: Dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {
            ex.submit(fetch_wp_category_posts, session, f"{SITE_ORIGIN}/flashcard/wp-json/wp/v2", cid): (cid, bucket)
            for cid, bucket, _ in FC_API_CAT_GROUPS
        }
        for fut in as_completed(futs):
            cid, bucket = futs[fut]
            plist = fut.result()
            fc_cat_posts[cid] = plist
            for p in plist:
                pid = p["id"]
                fc_posts_by_id[pid] = p
                if pid not in fc_post_bucket or bucket == "grammar":
                    fc_post_bucket[pid] = bucket

    print(f"  Fetched {len(main_posts_by_id)} main-site posts and {len(fc_posts_by_id)} flashcard posts via API.")

    # Register all API posts in PathRegistry
    for pid, p in main_posts_by_id.items():
        link_low = p["link"].lower()
        if "jlpt-n1" in link_low:
            bucket = "n1"
        elif "jlpt-n2" in link_low:
            bucket = "n2"
        elif "jlpt-n3" in link_low:
            bucket = "n3"
        else:
            bucket = main_post_bucket[pid]
        main_post_bucket[pid] = bucket
        slug = p.get("slug") or urllib.parse.urlsplit(p["link"]).path.strip("/")
        fname = slug_to_ascii(slug, str(pid))
        if bucket in ("n1", "n2", "n3", "extra"):
            rel = f"tests/{bucket}/{fname}"
        elif bucket == "kanji":
            rel = f"kanji/{fname}"
        else:
            rel = f"pages/{fname}"
        registry.register(p["link"], rel, post_id=pid, is_flashcard=False)

    for pid, p in fc_posts_by_id.items():
        bucket = fc_post_bucket[pid]
        slug = p.get("slug") or urllib.parse.urlsplit(p["link"]).path.strip("/")
        fname = slug_to_ascii(slug, str(pid))
        rel = f"flashcard/{bucket}/{fname}"
        registry.register(p["link"], rel, post_id=pid, is_flashcard=True)

    # Register & generate local category index pages for infographic & flashcard categories
    generated_cat_pages: List[Tuple[str, str, str, str]] = []  # (orig_url, rel, title, inner_html)

    # 1) Kanji & Vocabulary Infographics category (ID 61)
    if 61 in main_cat_info:
        cname, clink, _ = main_cat_info[61]
        crel = "categories/infographics/vocabulary-list.html"
        registry.register(clink, crel)
        registry.url_to_rel.setdefault(canonicalize_url(f"{SITE_ORIGIN}/category/infographics/"), crel)
        items_html = "".join(
            f'<li><a href="{html_lib.escape(p["link"])}">{clean_text_title(p["title"]["rendered"])}</a></li>\n'
            for p in main_cat_posts.get(61, [])
        )
        inner = (
            f'<div class="post clearfix"><h2 class="title">{html_lib.escape(cname)} ({len(main_cat_posts.get(61, []))} bài)</h2>'
            f'<div class="entry clearfix"><ul class="offline-list-columns">{items_html}</ul></div></div>'
        )
        generated_cat_pages.append((clink, crel, f"{cname} - Japanesetest4you.com", inner))

    # 2) Flashcard categories
    for cid, _, _ in FC_API_CAT_GROUPS:
        if cid not in fc_cat_info:
            continue
        cname, clink, cslug = fc_cat_info[cid]
        crel = f"categories/flashcard/{cslug}.html"
        registry.register(clink, crel)
        plist = sorted(fc_cat_posts.get(cid, []), key=lambda x: clean_text_title(x["title"]["rendered"]).lower())
        items_html = "".join(
            f'<li><a href="{html_lib.escape(p["link"])}">{clean_text_title(p["title"]["rendered"])}</a></li>\n'
            for p in plist
        )
        inner = (
            f'<div class="post clearfix"><h2 class="title">{html_lib.escape(cname)} ({len(plist)} bài)</h2>'
            f'<div class="entry clearfix"><ul class="offline-list-columns">{items_html}</ul></div></div>'
        )
        generated_cat_pages.append((clink, crel, f"{cname} - Japanesetest4you.com", inner))

    # 3) Special N2 Vocabulary Flashcards list page (since online jlpt-n2-vocabulary-list has plain text only)
    n2_vocab_fc_url = f"{SITE_ORIGIN}/jlpt-n2-vocabulary-flashcard-list/"
    n2_vocab_fc_rel = "lists/jlpt-n2-vocabulary-flashcard-list.html"
    registry.register(n2_vocab_fc_url, n2_vocab_fc_rel)
    n2_fc_plist = sorted(fc_cat_posts.get(19, []), key=lambda x: clean_text_title(x["title"]["rendered"]).lower())
    n2_items_html = "".join(
        f'<li><a href="{html_lib.escape(p["link"])}">{clean_text_title(p["title"]["rendered"])}</a></li>\n'
        for p in n2_fc_plist
    )
    n2_fc_inner = (
        f'<div class="post clearfix"><h2 class="title">JLPT N2 Vocabulary Flashcards ({len(n2_fc_plist)} từ vựng chi tiết)</h2>'
        f'<div class="entry clearfix"><p>Danh sách đầy đủ {len(n2_fc_plist)} trang flashcard từ vựng JLPT N2 (bản web gốc chỉ có danh sách chữ thuần):</p>'
        f'<ul class="offline-list-columns">{n2_items_html}</ul></div></div>'
    )
    generated_cat_pages.append((n2_vocab_fc_url, n2_vocab_fc_rel, "JLPT N2 Vocabulary Flashcards - Japanesetest4you.com", n2_fc_inner))

    # Inject CTA link into jlpt-n2-vocabulary-list.html
    n2_vlist_canon = canonicalize_url(f"{SITE_ORIGIN}/jlpt-n2-vocabulary-list/")
    if n2_vlist_canon in direct_pages:
        r, t, inn = direct_pages[n2_vlist_canon]
        cta = (
            f'<div class="offline-n2-vocab-cta"><strong>Mục lục chi tiết Offline:</strong> '
            f'<a href="{n2_vocab_fc_url}">Mở danh sách {len(n2_fc_plist)} trang Flashcard từ vựng JLPT N2 (có câu ví dụ &amp; nghĩa chi tiết) &raquo;</a></div>'
        )
        inn = inn.replace('<div class="entry clearfix">', '<div class="entry clearfix">\n' + cta, 1)
        direct_pages[n2_vlist_canon] = (r, t, inn)

    # Enhance flashcard/index.html with quick category hub
    fc_home_canon = canonicalize_url(f"{SITE_ORIGIN}/flashcard/")
    if fc_home_canon in direct_pages:
        r, t, inn = direct_pages[fc_home_canon]
        fc_hub = '<div class="offline-dashboard"><h2>Flashcard JLPT N3 &ndash; N2 &ndash; N1</h2><ul>'
        for cid, _, _ in FC_API_CAT_GROUPS:
            if cid in fc_cat_info and cid in (29, 22, 27, 20, 19, 18):
                cname, clink, _ = fc_cat_info[cid]
                fc_hub += f'<li><a href="{clink}">{html_lib.escape(cname)} ({len(fc_cat_posts.get(cid, []))})</a></li>'
        fc_hub += "</ul></div>"
        direct_pages[fc_home_canon] = (r, t, fc_hub + "\n" + inn)

    # Phase D: Gap-fill any remaining linked lessons/tests/kanji/flashcard pages
    print("[Phase D] Scanning links across all fetched pages for gap-fill...")
    candidate_urls: Set[str] = set()
    skip_hub_canons = {canonicalize_url(home_url), fc_home_canon}
    for canon_u, (_, _, inner) in direct_pages.items():
        if canon_u not in skip_hub_canons:
            candidate_urls.update(extract_candidate_links(inner, canon_u))
    for p in main_posts_by_id.values():
        candidate_urls.update(extract_candidate_links(p["content"]["rendered"], p["link"]))
    for p in fc_posts_by_id.values():
        candidate_urls.update(extract_candidate_links(p["content"]["rendered"], p["link"]))

    missing_urls = sorted(u for u in candidate_urls if registry.lookup(u) is None)
    print(f"  Found {len(missing_urls)} linked URLs not yet downloaded; fetching...")

    gapfill_pages: Dict[str, Tuple[str, str, str]] = {}
    if missing_urls:
        with ThreadPoolExecutor(max_workers=14) as ex:
            fut_map = {ex.submit(fetch_text, session, u): u for u in missing_urls}
            for fut in as_completed(fut_map):
                u = fut_map[fut]
                raw = fut.result()
                if not raw:
                    continue
                inner = extract_inner_content(raw)
                if not inner:
                    continue
                slug_part = urllib.parse.urlsplit(u).path.strip("/").split("/")[-1]
                uid = url_hash(u)
                if "/flashcard/" in u:
                    sub = "grammar" if "grammar" in u else "vocab"
                    rel = f"flashcard/{sub}/{slug_to_ascii(slug_part, uid)}"
                elif "kanji" in u:
                    rel = f"kanji/{slug_to_ascii(slug_part, uid)}"
                elif "jlpt-n1" in u:
                    rel = f"tests/n1/{slug_to_ascii(slug_part, uid)}"
                elif "jlpt-n2" in u:
                    rel = f"tests/n2/{slug_to_ascii(slug_part, uid)}"
                elif "jlpt-n3" in u:
                    rel = f"tests/n3/{slug_to_ascii(slug_part, uid)}"
                else:
                    rel = f"pages/{slug_to_ascii(slug_part, uid)}"
                registry.register(u, rel)
                gapfill_pages[u] = (rel, extract_page_title(raw), inner)

    print(f"  Gap-filled {len(gapfill_pages)} additional pages.")

    # Build prev/next navigation lookup per primary category
    main_nav: Dict[int, Tuple[Optional[dict], Optional[dict]]] = {}
    for cid, plist in main_cat_posts.items():
        for idx_p, p in enumerate(plist):
            if p["id"] not in main_nav:
                nxt = plist[idx_p - 1] if idx_p > 0 else None
                prv = plist[idx_p + 1] if idx_p + 1 < len(plist) else None
                main_nav[p["id"]] = (prv, nxt)

    fc_nav: Dict[int, Tuple[Optional[dict], Optional[dict]]] = {}
    for cid, plist in fc_cat_posts.items():
        for idx_p, p in enumerate(plist):
            if p["id"] not in fc_nav:
                nxt = plist[idx_p - 1] if idx_p > 0 else None
                prv = plist[idx_p + 1] if idx_p + 1 < len(plist) else None
                fc_nav[p["id"]] = (prv, nxt)

    # Assemble all pages to write + build search index
    search_entries: List[Dict[str, str]] = []
    pages_to_write: List[Tuple[str, str, str, str, str]] = []  # (rel, title, inner, orig_url, body_cls)

    for canon_u, (rel, title, inner) in direct_pages.items():
        pages_to_write.append((rel, title, inner, canon_u, "archive category"))
        if rel.startswith(("lists/", "categories/")) and "-page-" not in rel:
            search_entries.append({"t": clean_text_title(title).replace(" – Japanesetest4you.com", ""), "p": rel, "c": "List/Cat"})

    for orig_u, rel, title, inner in generated_cat_pages:
        pages_to_write.append((rel, title, inner, orig_u, "archive category"))
        search_entries.append({"t": clean_text_title(title).replace(" - Japanesetest4you.com", ""), "p": rel, "c": "Index"})

    for pid, p in main_posts_by_id.items():
        canon_u = canonicalize_url(p["link"])
        rel = registry.lookup(canon_u)
        if not rel:
            continue
        cat_links = [
            (main_cat_info[c][0], main_cat_info[c][1])
            for c in p.get("categories", [])
            if c in main_cat_info
        ]
        prv, nxt = main_nav.get(pid, (None, None))
        inner = render_post_content(p, cat_links, prv, nxt)
        t_clean = clean_text_title(p["title"]["rendered"])
        pages_to_write.append((rel, f"{t_clean} - Japanesetest4you.com", inner, p["link"], "single single-post"))
        bucket_tag = main_post_bucket[pid].upper()
        search_entries.append({"t": t_clean, "p": rel, "c": bucket_tag})

    for pid, p in fc_posts_by_id.items():
        canon_u = canonicalize_url(p["link"])
        rel = registry.lookup(canon_u)
        if not rel:
            continue
        cat_links = [
            (fc_cat_info[c][0], fc_cat_info[c][1])
            for c in p.get("categories", [])
            if c in fc_cat_info
        ]
        prv, nxt = fc_nav.get(pid, (None, None))
        inner = render_post_content(p, cat_links, prv, nxt)
        t_clean = clean_text_title(p["title"]["rendered"])
        pages_to_write.append((rel, f"{t_clean} - Japanesetest4you.com", inner, p["link"], "single single-post"))
        bucket_tag = "Grammar" if fc_post_bucket[pid] == "grammar" else "Vocab"
        search_entries.append({"t": t_clean, "p": rel, "c": bucket_tag})

    for canon_u, (rel, title, inner) in gapfill_pages.items():
        pages_to_write.append((rel, title, inner, canon_u, "single single-post"))
        search_entries.append({"t": clean_text_title(title).replace(" – Japanesetest4you.com", ""), "p": rel, "c": "Lesson"})

    # Write search-index.js
    search_js = "window.OFFLINE_SEARCH_INDEX = " + json.dumps(search_entries, ensure_ascii=False) + ";"
    (BASE_DIR / "assets" / "js" / "search-index.js").write_text(search_js, encoding="utf-8")

    # Render and write all HTML files
    print(f"[Phase E] Rendering and writing {len(pages_to_write)} HTML files...")

    def write_one(item: Tuple[str, str, str, str, str]) -> str:
        rel, title, inner, orig_u, body_cls = item
        out_path = BASE_DIR / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_page(title, inner, rel, orig_u, sidebar_html, footer_html, registry, body_cls)
        out_path.write_text(rendered, encoding="utf-8")
        return rel

    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(write_one, pages_to_write))

    elapsed = time.time() - t_start
    print(f"Done! Generated {len(pages_to_write)} offline HTML pages in {elapsed:.1f}s.")


if __name__ == "__main__":
    main()
