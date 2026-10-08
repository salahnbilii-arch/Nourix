#!/usr/bin/env python3
"""Bundle the edtech wedge (Physique-Chimie, Darija-first) static site into a
single Cloudflare Worker script and optionally deploy it.

Bundles ALL chapitreN.html pages found in this directory, inlining styles.css
and each page's referenced quizN.js / simN.js.

Usage:
    build.py            # build only -> worker-edtech.js
    build.py --deploy   # build + deploy as `edtech-wedge` worker
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

css = open(os.path.join(HERE, "styles.css"), encoding="utf-8").read()
motion_js = open(os.path.join(HERE, "motion.js"), encoding="utf-8").read().replace("</script", "<\\/script")
hero3d_js = open(os.path.join(HERE, "hero3d.js"), encoding="utf-8").read().replace("</script", "<\\/script")
theme_js = open(os.path.join(HERE, "theme.js"), encoding="utf-8").read().replace("</script", "<\\/script")

SCRIPT_RE = re.compile(r'<script\s+src="([^"]+\.js)"></script>')

# Scroll-reveal classes injected at build time (motion.js activates them).
# Uses the independent `translate` property in CSS, so theme `transform`
# hovers/rotations never conflict.
RV_REPLACEMENTS = [
    ('<h2 class="sec">', '<h2 class="sec rv">'),
    ('<h2 class="section-title">', '<h2 class="section-title rv">'),
    ('<div class="formula"', '<div class="formula rv"'),
    ('<span class="formula"', '<span class="formula rv"'),
    ('<div class="toolbox"', '<div class="toolbox rv"'),
    ('<div class="warn"', '<div class="warn rv"'),
    ('<div class="sim-box"', '<div class="sim-box rv"'),
    ('<a class="card"', '<a class="card rv"'),
    ('<div class="exam-card"', '<div class="exam-card rv"'),
    ('<div class="exam-banner"', '<div class="exam-banner rv"'),
]

# YouTube offline handler
yt_offline_js = open(os.path.join(HERE, 'yt-offline.js'), encoding='utf-8').read()
# Nourix account system (username + PIN): frontend modal + cloud sync, inlined into every page
account_js = open(os.path.join(HERE, 'account.js'), encoding='utf-8').read().replace("</script", "<\\/script")
# Nourix splash screen — Meta-style, bulletproof dismissal
splash_js = '''
<div id="nourix-splash">
  <div class="splash-center"><div class="splash-logo">Nourix</div></div>
  <div class="splash-bottom"><img src="from-numa-logo.png" alt="from numa" class="splash-numa-logo"></div>
</div>
<script>
(function(){
  var done = false;
  function dismiss(){
    if (done) return; done = true;
    var s = document.getElementById('nourix-splash');
    if (s) { s.classList.add('hide'); setTimeout(function(){ if(s.parentNode) s.parentNode.removeChild(s); }, 450); }
  }
  setTimeout(dismiss, 1200);
  if (document.readyState === 'complete') { setTimeout(dismiss, 400); }
  else { window.addEventListener('load', function(){ setTimeout(dismiss, 400); }); }
  setTimeout(dismiss, 2500);
})();
</script>
'''

# Phase 5 — Focus Mode (Mode-Zed killer) + enhanced missions + edge badges
focus_js = open(os.path.join(HERE, 'focus-mode.js'), encoding='utf-8').read().replace("</script", "<\\/script")
missions_js = open(os.path.join(HERE, 'missions.js'), encoding='utf-8').read().replace("</script", "<\\/script")

MOTION_SNIPPET = (
    '<noscript><style>.rv{opacity:1 !important;translate:none !important;scale:1 !important}</style></noscript>\n'
    '<script>\n' + motion_js + '\n</script>\n'
    '<script>\n' + yt_offline_js + '\n</script>\n'
    '<div class="numa-footer"><img src="from-numa-logo.png" alt="from numa" class="numa-footer-logo"></div>\n'
    '<script>\n' + account_js + '\n</script>\n'
    '<script>\n' + focus_js + '\n</script>\n'
    '<script>\n' + missions_js + '\n</script>\n'
    '<script>\n' + theme_js + '\n</script>\n'
)

# Theme boot: runs synchronously in <head> right after the CSS so the
# saved theme applies before first paint (no dark->light flash).
THEME_BOOT = (
    '<script>\n'
    'try{var _t=localStorage.getItem("nourix-theme")||"dark";'
    'var _r=_t==="auto"?(window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"):_t;'
    'if(_r==="light"||_r==="blue")document.documentElement.setAttribute("data-theme",_r);'
    '}catch(e){}\n'
    '</script>\n'
)

HERO3D_SNIPPET = (
    '<script src="/three.min.js"></script>\n'
    '<script>\n' + hero3d_js + '\n</script>\n'
)

HERO_CANVAS = (
    '<section class="hero hero-3d">'
    '<canvas class="hero-canvas" id="hero3d" aria-hidden="true"></canvas>'
    '<span class="orb orb-a" aria-hidden="true"></span>'
    '<span class="orb orb-b" aria-hidden="true"></span>'
    '<span class="orb orb-c" aria-hidden="true"></span>'
)

TICKER_ITEMS = (
    '<span><b>c</b> = 299 792 458 m/s</span>'
    '<span><b>E</b> = mc²</span>'
    '<span><b>v</b> = d / Δt</span>'
    '<span><b>f</b> = 1 / T</span>'
    '<span><b>λ</b> = v / T</span>'
    '<span><b>P</b> = U × I</span>'
    '<span><b>Q</b> = C × U</span>'
    '<span><b>τ</b> = R × C</span>'
)
TICKER = (
    '<div class="ticker rv" aria-hidden="true"><div class="ticker-track">'
    + TICKER_ITEMS + TICKER_ITEMS +
    '</div></div>'
)

FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500..800'
    '&family=IBM+Plex+Sans:wght@400;600;700'
    '&family=IBM+Plex+Sans+Arabic:wght@400;600;700&display=swap" rel="stylesheet">\n'
)


def inline(page, fname):
    page = page.replace(
        '<link rel="stylesheet" href="styles.css">',
        FONT_LINKS + "<style>\n" + css + "\n</style>\n" + THEME_BOOT,
    )

    def repl(m):
        src = m.group(1)
        if src == "three.min.js":
            return m.group(0)  # keep as external, served via /three.min.js route
        path = os.path.join(HERE, src)
        if not os.path.exists(path):
            print(f"WARNING: missing {src}, leaving tag as-is", file=sys.stderr)
            return m.group(0)
        js = open(path, encoding="utf-8").read().replace("</script", "<\\/script")
        return "<script>\n" + js + "\n</script>"

    page = SCRIPT_RE.sub(repl, page)

    for old, new in RV_REPLACEMENTS:
        page = page.replace(old, new)

    tail = MOTION_SNIPPET
    # Nourix splash: inject after <body>
    page = page.replace('<body>', '<body>' + splash_js.split('<script>')[0], 1)
    if fname.startswith("index"):
        page = page.replace(
            '<section class="hero">', HERO_CANVAS, 1,
        )
        # first </section> closes the hero -> ticker right under it
        page = page.replace("</section>", "</section>" + TICKER, 1)
        tail = HERO3D_SNIPPET + MOTION_SNIPPET
    if "</body>" in page:
        page = page.replace("</body>", tail + "</body>")
    else:
        page += tail
    return page


pages = {}
for fname in sorted(os.listdir(HERE)):
    # index + every chapter page incl. language variants (chapitreN-fr/ar/en.html)
    if fname.endswith(".html") and fname != "worker-edtech.js":
        with open(os.path.join(HERE, fname), encoding="utf-8") as f:
            pages["/" + fname] = inline(f.read(), fname)

# Static vendor assets served as their own routes (not inlined into pages)
assets = {}
root_three = os.path.join(HERE, "three.min.js")
if os.path.exists(root_three):
    with open(root_three, "rb") as f:
        assets["/three.min.js"] = f.read()
    print(f"root: three.min.js {len(assets['/three.min.js'])//1024} KB", file=sys.stderr)
vendor_three = os.path.join(HERE, "vendor", "three.min.js")
if os.path.exists(vendor_three):
    with open(vendor_three, "rb") as f:
        assets["/three.min.js"] = f.read()
    print(f"vendor: three.min.js {len(assets['/three.min.js'])//1024} KB", file=sys.stderr)
# App version for offline APK update checks
version_file = os.path.join(HERE, "app-version.txt")
if os.path.exists(version_file):
    with open(version_file, "rb") as f:
        assets["/app-version.txt"] = f.read()
    print(f"app-version.txt bundled", file=sys.stderr)
# Nourix brand logo (from numa official)
logo_file = os.path.join(HERE, "from-numa-logo.png")
if os.path.exists(logo_file):
    with open(logo_file, "rb") as f:
        assets["/from-numa-logo.png"] = f.read()
    print(f"from-numa-logo.png bundled {len(assets['/from-numa-logo.png'])//1024} KB", file=sys.stderr)
# Offline APK for in-app auto-updates
# APK bundling DISABLED (2026-10-08): 11MB APK exceeds 64MB worker limit
# APK distributed via chat/direct link instead
# apk_file = os.path.join(HERE, "edtech-app-offline.apk")
# if os.path.exists(apk_file):
#     with open(apk_file, "rb") as f:
#         assets["/app.apk"] = f.read()
#     print(f"app.apk bundled {len(assets['/app.apk'])//1024//1024} MB", file=sys.stderr)

print(f"bundled {len(pages)} pages: {sorted(pages)}", file=sys.stderr)

routes = []
for path in sorted(pages):
    const = "PAGE_" + re.sub(r"\W", "_", path).upper().strip("_")
    routes.append((path, const))

worker = (
    "export default {\n"
    '  async fetch(request, env) {\n'
    "    const url = new URL(request.url);\n"
    '    let p = url.pathname;\n'
    '    if (p === "/") p = "/index.html";\n'
    '    /* AI Exercise Solver API */\n'
    '    if (p === "/api/solve") { return await handleSolve(request, env); }\n'
    '    /* Nourix Account API (username + PIN, D1-backed) */\n'
    '    if (p === "/api/register" || p === "/api/login" || p === "/api/sync" || p === "/api/parent") { return await handleAccount(request, env); }\n'
    '    /* Google OAuth (GIS ID token verification) */\n'
    '    if (p === "/api/google-auth") { return await handleGoogleAuth(request, env); }\n'
    '    /* Public config (Google Client ID for GIS button) */\n'
    '    if (p === "/api/config") { return new Response(JSON.stringify({ googleClientId: (env.GOOGLE_CLIENT_ID||"").trim() }), { headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=300" } }); }\n'
)
for path, const in routes:
    worker += (
        f'    if (p === "{path}") {{\n'
        f"      return new Response({const}, {{ headers: SEC_HEADERS }});\n"
        "    }\n"
    )
# vendor asset routes (three.js for 3D labs) + binary assets (logo PNG, APK)
# Binary assets are base64-encoded to survive JSON embedding
import base64
BINARY_ASSETS = {"/from-numa-logo.png": "image/png", "/app.apk": "application/vnd.android.package-archive"}
for apath in sorted(assets):
    aconst = "ASSET_" + re.sub(r"\W", "_", apath).upper().strip("_")
    if apath in BINARY_ASSETS:
        ctype = BINARY_ASSETS[apath]
        worker += (
            f'    if (p === "{apath}") {{\n'
            f'      const bin = Uint8Array.from(atob({aconst}), c => c.charCodeAt(0));\n'
            f'      return new Response(bin, {{ headers: {{ "Content-Type": "{ctype}", "Cache-Control": "public, max-age=31536000, immutable", "Content-Disposition": "attachment; filename=\\"nourix.apk\\"" }} }});\n'
            "    }\n"
        )
    else:
        worker += (
            f'    if (p === "{apath}") {{\n'
            f"      return new Response({aconst}, {{ headers: ASSET_HEADERS }});\n"
            "    }\n"
        )
worker += (
    '    return new Response("Not found", { status: 404 });\n'
    "  }\n"
    "};\n"
    "const SEC_HEADERS = {\n"
    '  "Content-Type": "text/html; charset=utf-8",\n'
    '  "Cache-Control": "public, max-age=60",\n'
    '  "Content-Security-Policy": "default-src \'self\'; script-src \'self\' \'unsafe-inline\' https://accounts.google.com https://apis.google.com; style-src \'unsafe-inline\' https://fonts.googleapis.com https://accounts.google.com; font-src https://fonts.gstatic.com; img-src \'self\' data: https://lh3.googleusercontent.com; frame-src \'self\' https://www.youtube-nocookie.com https://docs.google.com https://accounts.google.com; connect-src \'self\' https://www.googleapis.com; frame-ancestors \'none\'; base-uri \'self\'",\n'
    '  "X-Frame-Options": "DENY",\n'
    '  "X-Content-Type-Options": "nosniff",\n'
    '  "Referrer-Policy": "strict-origin-when-cross-origin",\n'
    '  "Permissions-Policy": "camera=(), microphone=(), geolocation()",\n'
    "};\n"
    "const ASSET_HEADERS = {\n"
    '  "Content-Type": "application/javascript; charset=utf-8",\n'
    '  "Cache-Control": "public, max-age=31536000, immutable",\n'
    "};\n"
)
for path, const in routes:
    worker += f"const {const} = " + json.dumps(pages[path], ensure_ascii=False) + ";\n"
for apath in sorted(assets):
    aconst = "ASSET_" + re.sub(r"\W", "_", apath).upper().strip("_")
    if apath in BINARY_ASSETS:
        worker += f"const {aconst} = " + json.dumps(base64.b64encode(assets[apath]).decode("ascii")) + ";\n"
    else:
        worker += f"const {aconst} = " + json.dumps(assets[apath].decode("utf-8", errors="replace")) + ";\n"

# AI Solver API (reads GEMINI_KEY from Worker secrets)
with open(os.path.join(HERE, "solver-api.js"), encoding="utf-8") as f:
    solver_src = f.read()
worker += "\n" + solver_src + "\n"

# Nourix Account API (D1 binding "DB" — qequk-db, schema self-initializes)
with open(os.path.join(HERE, "account-api.js"), encoding="utf-8") as f:
    account_src = f.read()
worker += "\n" + account_src + "\n"

# Google OAuth verification (GIS ID tokens)
with open(os.path.join(HERE, "google-auth.js"), encoding="utf-8") as f:
    gauth_src = f.read()
worker += "\n" + gauth_src + "\n"

out = os.path.join(HERE, "worker-edtech.js")
with open(out, "w", encoding="utf-8") as f:
    f.write(worker)
print(f"bundled {len(worker)/1024:.0f} KB -> {out}")

if "--deploy" in sys.argv:
    # cf_deploy.py requires a D1 ref; the site is fully static so we bind the
    # existing qequk-db without using it.
    r = subprocess.run(
        [sys.executable,
         os.path.expanduser("~/workspace/skills/cloudflare/bin/cf_deploy.py"),
         "edtech-wedge", out, "qequk-db"],
        capture_output=True, text=True, timeout=180)
    print(r.stdout[-800:])
    print(r.stderr[-300:], file=sys.stderr)
    if r.returncode != 0:
        sys.exit("deploy failed")
