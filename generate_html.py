#!/usr/bin/env python3
"""Generate index.html from configuration files and a Jinja2 template."""

import base64
import hashlib
import json
import re

from jinja2 import Environment, FileSystemLoader

from config_loader import load_podcast_config, load_hosts_config, load_credits_config, load_themes_config

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _build_trace_jsonld(podcast_config):
    trace_cfg = podcast_config.get("trace", {})
    if not trace_cfg:
        return ""
    trace_obj = {
        "@context": "https://tracestandard.org/schema/v1",
        "@type": "TRACEAssessment",
        "contentTitle": podcast_config["title"],
        "contentURL": podcast_config["url"],
        "contentType": "series",
        "producer": podcast_config["author"],
        "producerURL": trace_cfg["producer_url"],
        "communityRepresented": trace_cfg["community"],
        "aiGenerated": trace_cfg.get("ai_generated", False),
        "aiTools": trace_cfg.get("ai_tools", []),
        "aiRole": trace_cfg.get("ai_role", "none"),
        "assessmentDate": trace_cfg["assessment_date"],
        "assessedBy": trace_cfg["assessed_by"],
        "track": trace_cfg["track"],
        "disqualified": trace_cfg.get("disqualified", False),
        "scores": {
            cat: {"score": s["score"], "max": s["max"]}
            for cat, s in trace_cfg.get("scores", {}).items()
        },
        "totalScore": trace_cfg["total_score"],
        "maxScore": trace_cfg["total_max"],
        "verdict": trace_cfg["verdict"],
        "frameworkVersion": trace_cfg.get("version", "1.0"),
    }
    return f'    <script type="application/ld+json">\n    {json.dumps(trace_obj, indent=2, ensure_ascii=False)}\n    </script>'


def generate_index_html():
    podcast_config = load_podcast_config()
    hosts_config = load_hosts_config()
    credits_config = load_credits_config()
    themes_config = load_themes_config()

    env = Environment(loader=FileSystemLoader("templates"), autoescape=False)
    template = env.get_template("index.html.j2")

    html_content = template.render(
        podcast=podcast_config,
        hosts=hosts_config,
        credits=credits_config,
        themes=themes_config,
        days=list(enumerate(DAYS_OF_WEEK)),
        themes_json=json.dumps(themes_config),
        trace_jsonld=_build_trace_jsonld(podcast_config),
    )

    # Compute SHA256 of each inline <script> block and substitute into the CSP.
    script_hashes = []
    for m in re.finditer(r'<script[^>]*>(.*?)</script>', html_content, re.DOTALL):
        body = m.group(1)
        sha256_bytes = hashlib.sha256(body.encode('utf-8')).digest()
        script_hashes.append(f"'sha256-{base64.b64encode(sha256_bytes).decode()}'")
    script_hash = ' '.join(script_hashes) if script_hashes else "'unsafe-inline'"
    html_content = html_content.replace('SCRIPT_HASH_PLACEHOLDER', script_hash)

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)

    print("✅ Generated index.html from config files")
    print(f"📄 Title: {podcast_config['title']}")
    print(f"🎙️  Hosts: {len(hosts_config)}")
    print(f"✨ Credits: {len(credits_config['html']['items'])} items")


if __name__ == "__main__":
    generate_index_html()
