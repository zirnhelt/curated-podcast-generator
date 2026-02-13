# Cariboo Signals Podcast - Roadmap

## Current State
- Daily generation at 5 AM Pacific via GitHub Actions
- Fetches scored articles from RSS feed system
- Two AI hosts: Riley (tech systems) and Casey (community development)
- 7 rotating weekly themes
- Music interludes via Sumo AI theme (intro/interval/outro)
- OpenAI TTS for host voices (nova, echo)

## Working Well
- Music interludes integrated via pydub
- Script polishing pass reduces repetition between segments
- Configuration externalized to config/ directory (hosts, themes, credits, interests)
- Deduplication against last 7 days of episodes
- Episode memory for continuity (21-day window)
- Citations system tracks sources per episode
- Indigenous territory acknowledgment in descriptions
- RSS feed with proper XML escaping

## Short-term
- [ ] Submit to Apple Podcasts (see [docs/submit-apple-podcasts.md](docs/submit-apple-podcasts.md))
  - [ ] Upgrade cover art to 1400x1400+ pixels (Apple minimum)
  - [ ] Replace placeholder email in config/podcast.json
  - [ ] Submit RSS feed at podcastsconnect.apple.com
- [ ] Submit to Spotify, Amazon Music, Pocket Casts
- [ ] Clean up backup and old generator scripts from root directory
- [ ] Reduce technical jargon for general audiences
- [ ] Theme-based filtering on website index page

## Medium-term
- [ ] Permanent episode memory with weighted recency (replace 21-day hard limit)
- [ ] Local holidays and events integration in episode openings
- [ ] Evolving stories context - flag when covering updates to previously discussed topics
- [ ] Better theme-to-article matching (currently just takes top 4 scored articles)

## Long-term / Speculative
- [ ] Listener feedback loop - topic requests or engagement signals shape future episodes
- [ ] Cross-project: shared interest/scoring config between RSS and Podcast systems
- [ ] Monetization: podcast sponsorships, premium episodes
- [ ] Multi-show support - same infrastructure, different regional focuses
- [ ] Consider Gemini for holistic podcast generation - may be possible on free tier
- [ ] Consider pydub for music integration and reducing API calls to Claude
