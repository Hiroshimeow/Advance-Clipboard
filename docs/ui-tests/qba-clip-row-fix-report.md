# QBA clip row fix report

## Scope
Validated the clip-row widget layout and async history-promotion behavior for the Advance-Clipboard UI.

## Findings
- `ui/widgets.py` already implements the required row structure as `[text content] [badge column] [action buttons]`.
- The action-button column is fixed-width and added with right alignment, so tools stay flush-right.
- The badge column is outside the text grid, so it no longer steals width from the text content.
- Pinned rows now use a minimum content height path that keeps the right-side tool stack visible.
- `main.py` keeps promote-to-top asynchronous via `QTimer.singleShot(0, lambda: self._promote_history_clip(clip_id))`.

## Verification run
- `python -m pytest tests/test_widgets_layout.py -q`
  - Result: `5 passed in 0.66s`
- `python tests/render_clip_layout_evidence.py`
  - Generated screenshots in `docs/ui-tests/`

## Evidence files
- `docs/ui-tests/clip-row-aligned.png`
- `docs/ui-tests/clip-row-pinned-tools.png`
- `docs/ui-tests/clip-row-3lines.png`
- `docs/ui-tests/clip-row-copy.png`

## Notes
The required UI/layout remediation was already present in the workspace when this execution task started. I only extended the offscreen evidence generator to emit the missing pinned-row screenshot required by this task.

## Additional improvements (hcu-lab worker)
- **Smooth scrolling** — Enhanced `SmoothListWidget` with per-pixel scroll mode (`ScrollPerPixel`) and direction-aware step calculation for smoother wheel scrolling.
- **Hover highlighting** — Added mouse tracking, hover enter/leave events, and visual feedback (blue border + dark background) when hovering over clip items.
- **Scroll to center on navigation** — Added `scrollToSelected()` method and integrated it into `nav_up`/`nav_down` in `ClipboardBrowserController` so arrow key navigation always centers the selected item in the viewport.
- **Hover state cleanup** — `_clear_hover()` is now called before list refresh and clip deletion to prevent crashes when hovering over deleted items.
