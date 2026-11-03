# Future Improvements

This file tracks planned enhancements for `Advance-Clipboard`.

## High Priority

- **UI Module Decomposition**: Split `main.py` further into focused controller modules for hotkeys, clipboard processing, and window lifecycle.
- **Settings Panel**: Add a GUI for hotkey, theme, backup, and startup preferences.
- **Pinned Popup Polish**: Continue improving resize affordances, search navigation, and screen-edge placement.
- **Context Menu Flow**: Make group and tag actions faster for large pinned lists.

## Search & Ranking

- **Search Diagnostics**: Show why a result matched, including title, tag, group, and text score sources.
- **Large History Performance**: Keep search and pagination responsive when the database grows beyond 50k clips.
- **Multilingual Optimizations**: Test and refine search behavior for non-English clipboard content.

## UX & Stability

- **Global Settings**: Keyboard shortcut customization.
- **Personalization**: Prefer results that the user has selected frequently in the past.
- **Backup Visibility**: Surface backup status, last export time, and recovery result in settings.
- **Import/Export Tools**: Add explicit user-controlled export and restore actions.
