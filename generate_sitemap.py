#!/usr/bin/env python3
"""
Generates sitemap.xml from all HTML files in the public/ folder.
Usage: python generate_sitemap.py
"""
from pathlib import Path
from datetime import date

project_dir = Path(__file__).resolve().parent
public_dir = project_dir / "public"
base_url = "https://familyholidayparks.com.au"
today = str(date.today())

urls = []

# Add homepage
urls.append({
    "loc": base_url,
    "lastmod": today,
    "priority": "1.0",
    "changefreq": "weekly"
})

# Add all location pages
for html_file in sorted(public_dir.glob("*.html")):
    slug = html_file.stem
    urls.append({
        "loc": f"{base_url}/{slug}",
        "lastmod": today,
        "priority": "0.8",
        "changefreq": "monthly"
    })

# Build XML
lines = ['<?xml version="1.0" encoding="UTF-8"?>']
lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')

for url in urls:
    lines.append("  <url>")
    lines.append(f'    <loc>{url["loc"]}</loc>')
    lines.append(f'    <lastmod>{url["lastmod"]}</lastmod>')
    lines.append(f'    <changefreq>{url["changefreq"]}</changefreq>')
    lines.append(f'    <priority>{url["priority"]}</priority>')
    lines.append("  </url>")

lines.append("</urlset>")

sitemap_path = public_dir / "sitemap.xml"
sitemap_path.write_text("\n".join(lines), encoding="utf-8")
print(f"Sitemap generated: {len(urls)} URLs → {sitemap_path}")
