"""Generate DigiSeva_User_Guide.pdf from README.md using weasyprint."""
import markdown
from weasyprint import HTML
from pathlib import Path

repo = Path(__file__).parent.parent
md_text = (repo / "README.md").read_text()

body_html = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

css = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'Inter', -apple-system, sans-serif;
  font-size: 10.5pt;
  line-height: 1.65;
  color: #1a1b1e;
  background: #ffffff;
}

.banner {
  background: linear-gradient(135deg, #1a1b1e 0%, #25262b 100%);
  color: #e8e9ed;
  border-radius: 10px;
  padding: 1.4rem 1.8rem;
  margin-bottom: 1.8rem;
  border-left: 5px solid #d4993e;
}
.banner-title {
  font-size: 22pt;
  font-weight: 700;
  color: #d4993e;
  letter-spacing: -0.5px;
  margin-bottom: 0.2rem;
}
.banner-sub {
  font-size: 10pt;
  color: #9ca3af;
  margin: 0;
}

/* Hide the h1 from markdown — shown in banner instead */
h1 { display: none; }

h2 {
  font-size: 13.5pt;
  font-weight: 700;
  color: #1a1b1e;
  margin: 1.6rem 0 0.6rem;
  padding-bottom: 0.35rem;
  border-bottom: 2.5px solid #d4993e;
}

h3 {
  font-size: 11pt;
  font-weight: 600;
  color: #1a1b1e;
  margin: 1.1rem 0 0.4rem;
}

p { margin: 0.4rem 0 0.6rem; }

a { color: #d4993e; text-decoration: none; }

strong { font-weight: 600; }

blockquote {
  background: #fffbeb;
  border-left: 4px solid #fcd34d;
  border-radius: 6px;
  padding: 0.65rem 1rem;
  margin: 0.8rem 0;
  color: #92400e;
  font-size: 9.5pt;
}
blockquote p { margin: 0; }

code {
  font-family: 'JetBrains Mono', 'Courier New', monospace;
  font-size: 9pt;
  background: #f3f4f6;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
  color: #b45309;
}

pre {
  background: #1e1e2e;
  color: #cdd6f4;
  border-radius: 8px;
  padding: 0.85rem 1.1rem;
  margin: 0.7rem 0;
}
pre code {
  background: none;
  color: inherit;
  padding: 0;
  font-size: 9pt;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.8rem 0 1rem;
  font-size: 9.5pt;
}
th {
  text-align: left;
  font-weight: 600;
  padding: 0.45rem 0.75rem;
  border-bottom: 2px solid #e5e7eb;
  color: #6c7086;
  text-transform: uppercase;
  font-size: 8pt;
  letter-spacing: 0.05em;
  background: #f9fafb;
}
td {
  padding: 0.4rem 0.75rem;
  border-bottom: 1px solid #e5e7eb;
}
tr:last-child td { border-bottom: none; }
tr:nth-child(even) { background: #fafafa; }

ul, ol { padding-left: 1.4rem; margin: 0.4rem 0 0.6rem; }
li { margin: 0.2rem 0; }

hr { border: none; border-top: 1px solid #e5e7eb; margin: 1.2rem 0; }

@page {
  size: A4;
  margin: 2cm 2.2cm 2cm 2.2cm;
  @bottom-center {
    content: "DigiSeva User Guide  ·  Page " counter(page) " of " counter(pages);
    font-size: 8pt;
    color: #9ca3af;
    font-family: 'Inter', sans-serif;
  }
}
"""

html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"/>
<style>{css}</style>
</head>
<body>
<div class="banner">
  <div class="banner-title">DigiSeva</div>
  <div class="banner-sub">Personal Finance Tracker — User Guide</div>
</div>
{body_html}
</body>
</html>"""

out = repo / "DigiSeva_User_Guide.pdf"
HTML(string=html).write_pdf(str(out))
print(f"PDF written: {out}  ({out.stat().st_size // 1024} KB)")
