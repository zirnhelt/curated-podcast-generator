#!/usr/bin/env python3
"""
Generate index.html from configuration files
Makes it easy to update website content without editing HTML
"""

from config_loader import load_podcast_config, load_hosts_config, load_credits_config

def generate_index_html():
    """Generate complete index.html from config files."""
    
    podcast_config = load_podcast_config()
    hosts_config = load_hosts_config()
    credits_config = load_credits_config()
    
    # Generate host cards HTML
    host_cards = ""
    for host_key, host_data in hosts_config.items():
        host_cards += f"""
            <div class="host-card">
                <div class="host-name">{host_data['emoji']} {host_data['name']}</div>
                <p>{host_data['full_bio']}</p>
            </div>
"""
    
    # Generate credits HTML
    credits_html = ""
    for item in credits_config['html']['items']:
        credits_html += f'                <p><strong>{item["label"]}:</strong> {item["value"]}</p>\n'
    
    html_content = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{podcast_config['title']} - {podcast_config['tagline']}</title>
    <meta name="description" content="{podcast_config['description']}">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: linear-gradient(135deg, #4a5d73 0%, #2c3e50 100%);
            min-height: 100vh;
            padding: 40px 20px;
        }}
        
        .container {{
            background: white;
            border-radius: 16px;
            padding: 48px;
            max-width: 900px;
            margin: 0 auto;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }}
        
        .header {{
            text-align: center;
            margin-bottom: 40px;
        }}
        
        .podcast-cover {{
            width: 200px;
            height: 200px;
            margin: 0 auto 24px;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
        }}
        
        h1 {{
            font-size: 2.5em;
            margin-bottom: 8px;
            color: #2c3e50;
        }}
        
        .subtitle {{
            margin-bottom: 32px;
            color: #666;
            font-size: 1.2em;
            font-style: italic;
        }}
        
        .theme-description {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #2c3e50;
            margin-bottom: 32px;
        }}
        
        .hosts-section {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 32px;
        }}
        
        .host-card {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #3498db;
        }}
        
        .host-name {{
            font-weight: bold;
            color: #2c3e50;
            margin-bottom: 8px;
            font-size: 1.1em;
        }}
        
        .episodes-section {{
            margin-top: 40px;
        }}
        
        .episodes-list {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            min-height: 200px;
        }}
        
        .episode-item {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #ddd;
        }}
        
        .episode-item:last-child {{
            border-bottom: none;
        }}
        
        .episode-title {{
            font-weight: 500;
            color: #2c3e50;
        }}
        
        .episode-date {{
            color: #666;
            font-size: 0.9em;
        }}
        
        .episode-audio {{
            margin-top: 8px;
        }}
        
        .loading {{
            text-align: center;
            color: #666;
            font-style: italic;
        }}
        
        .subscribe-section {{
            text-align: center;
            margin-top: 32px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
        }}
        
        .podcast-apps {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-top: 20px;
        }}
        
        .app-link {{
            display: block;
            background: white;
            padding: 12px;
            border-radius: 8px;
            text-decoration: none;
            color: #333;
            border: 2px solid #ddd;
            transition: all 0.3s;
        }}
        
        .app-link:hover {{
            border-color: #3498db;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }}
        
        .app-name {{
            font-weight: bold;
            margin-bottom: 4px;
        }}
        
        .app-description {{
            font-size: 0.9em;
            color: #666;
        }}
        
        .rss-links {{
            text-align: center;
            margin-top: 20px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
        }}
        
        .rss-button {{
            display: inline-block;
            background: #e74c3c;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 6px;
            margin: 0 8px;
            font-weight: 500;
            transition: background 0.3s;
        }}
        
        .rss-button:hover {{
            background: #c0392b;
        }}
        
        .footer {{
            text-align: center;
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #ddd;
            color: #666;
            font-size: 0.9em;
        }}
        
        .credits {{
            background: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        
        .credits h3 {{
            margin-bottom: 16px;
            color: #2c3e50;
        }}
        
        .credits-content {{
            text-align: left;
            max-width: 600px;
            margin: 0 auto;
        }}
        
        @media (max-width: 768px) {{
            .container {{
                padding: 24px;
            }}
            
            .hosts-section {{
                grid-template-columns: 1fr;
            }}
            
            .podcast-apps {{
                grid-template-columns: 1fr;
            }}
            
            h1 {{
                font-size: 2em;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <img src="{podcast_config['cover_image']}" alt="{podcast_config['title']} Cover" class="podcast-cover">
            <h1>{podcast_config['title']}</h1>
            <p class="subtitle">{podcast_config['tagline']}</p>
        </div>
        
        <div class="theme-description">
            <p>{podcast_config['description']}</p>
        </div>
        
        <div class="hosts-section">{host_cards}
        </div>
        
        <div class="episodes-section">
            <h2>Recent Episodes</h2>
            <div class="episodes-list" id="episodes-container">
                <div class="loading">Loading episodes...</div>
            </div>
        </div>
        
        <div class="subscribe-section">
            <h3>Subscribe to {podcast_config['title']}</h3>
            <p>Get new episodes automatically in your favorite podcast app:</p>
            
            <p style="color: #666; font-style: italic; margin-top: 12px;">
                Podcast app listings coming soon. For now, subscribe directly via RSS below.
            </p>
            
            <div class="rss-links">
                <h4>Or subscribe directly:</h4>
                <a href="podcast-feed.xml" class="rss-button">🎙️ RSS Feed</a>
                <a href="{podcast_config['feed_url']}" class="rss-button">📡 Direct Link</a>
            </div>
        </div>
        
        <div class="footer">
            <div class="credits">
                <h3>{credits_config['html']['heading']}</h3>
                <div class="credits-content">
{credits_html}
                </div>
            </div>
            
            <p>Generated automatically from curated RSS feeds • Updated daily at 5 AM PST</p>
            <p>Part of the <a href="https://zirnhelt.github.io/super-rss-feed/">Super RSS Feed</a> project</p>
        </div>
    </div>

    <script>
        async function loadEpisodes() {{
            try {{
                const response = await fetch('podcast-feed.xml');
                const xmlText = await response.text();
                
                const parser = new DOMParser();
                const xmlDoc = parser.parseFromString(xmlText, 'text/xml');
                const items = xmlDoc.querySelectorAll('item');
                
                const container = document.getElementById('episodes-container');
                
                if (items.length === 0) {{
                    container.innerHTML = `
                        <div class="loading">
                            <p>No episodes found yet.</p>
                            <p style="margin-top: 16px;"><em>The podcast generator creates new episodes daily based on curated RSS feed data.</em></p>
                        </div>
                    `;
                    return;
                }}
                
                let episodesHTML = '';
                
                items.forEach((item, index) => {{
                    const title = item.querySelector('title')?.textContent || 'Untitled Episode';
                    const pubDate = item.querySelector('pubDate')?.textContent || '';
                    const enclosure = item.querySelector('enclosure');
                    const audioUrl = enclosure?.getAttribute('url') || '';
                    
                    let formattedDate = pubDate;
                    try {{
                        const date = new Date(pubDate);
                        formattedDate = date.toLocaleDateString('en-US', {{ 
                            weekday: 'short', 
                            year: 'numeric', 
                            month: 'short', 
                            day: 'numeric' 
                        }});
                    }} catch (e) {{
                        // Keep original date if parsing fails
                    }}
                    
                    episodesHTML += `
                        <div class="episode-item">
                            <div>
                                <div class="episode-title">${{title}}</div>
                                <div class="episode-date">${{formattedDate}}</div>
                                ${{audioUrl ? `
                                    <div class="episode-audio">
                                        <audio controls style="width: 100%; max-width: 300px;">
                                            <source src="${{audioUrl}}" type="audio/mpeg">
                                            Your browser does not support the audio element.
                                        </audio>
                                    </div>
                                ` : ''}}
                            </div>
                        </div>
                    `;
                }});
                
                container.innerHTML = episodesHTML;
                
            }} catch (error) {{
                console.error('Failed to load episodes:', error);
                document.getElementById('episodes-container').innerHTML = `
                    <div class="loading">
                        <p>Episodes are being generated...</p>
                        <p style="margin-top: 16px;"><em>The podcast generator creates new episodes daily based on curated RSS feed data.</em></p>
                    </div>
                `;
            }}
        }}
        
        loadEpisodes();
    </script>
</body>
</html>'''
    
    # Save to file
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print("✅ Generated index.html from config files")
    print(f"📄 Title: {podcast_config['title']}")
    print(f"🎙️  Hosts: {len(hosts_config)}")
    print(f"✨ Credits: {len(credits_config['html']['items'])} items")

if __name__ == "__main__":
    generate_index_html()
