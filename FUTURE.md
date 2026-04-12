# Future Improvements

This file tracks planned enhancements for `Advance-Clipboard`.

## High Priority

- **UI Module Decomposition**: Split `main.py` further into focused controller modules (hotkeys, clipboard processing, window lifecycle).
- **Settings Panel**: Add a GUI to configure `neural/config.json` without editing files.
- **Node Interaction**: Improve node click behavior to directly load and focus the clip in the UI instead of just searching for it.
- **Theme Support**: Add more color themes for the Galaxy map (Matrix, Nebula, Sunset).

## Search & Indexing

- **Search Diagnostics**: Show why a result matched (score source and ranking breakdown).
- **Corpus Size Threshold**: Keep history semantic search limited or sampled when the database grows beyond 50k+ clips.
- **Multilingual Optimizations**: Test and refine search behavior for non-English clipboard content.

## AI & LLM (Optional/Opt-in)

- **LLM Query Rewriting**: Optionally expand vague queries into better semantic search terms.
- **Auto-Tagging**: LLM-assisted suggestions for tags and groups based on clip content.
- **Summarization**: Provide a "Summary of recent clips" view.

## UX & Stability

- **Global Settings**: Keyboard shortcut customization.
- **Personalization**: Prefer results that the user has selected frequently in the past.
- **Performance**: Throttle D3.js animations on lower-end machines if many nodes are visible.
