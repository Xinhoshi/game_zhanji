# Design

## Visual Theme

Dark operational dashboard with a restrained full-palette data vocabulary. The scene is a late-evening desktop review session: the user is focused, comparing fresh screenshots and correcting OCR before the weekly Boss data is archived. The UI should feel stable, precise, and slightly premium, with warmth reserved for active report signals.

## Color

Use OKLCH tokens where possible. The base is tinted charcoal, not pure black. Warm amber marks active highlights and archive/report status. Cool cyan marks Boss data. Green marks positive deltas and successful correction. Red marks errors or missing data.

## Typography

Use a system sans stack optimized for Chinese and mixed Latin/Japanese names: `-apple-system`, `BlinkMacSystemFont`, `"Segoe UI"`, `"Microsoft YaHei UI"`, `"Noto Sans CJK SC"`, system-ui, sans-serif. Use fixed rem scales for dashboard predictability. Use tabular numbers for rank, power, damage, and timestamps.

## Layout

The page uses a fixed top header and a single dashboard shell. The first viewport is a two-row overview: report identity plus upload on top, four metric cells below, then two compact leaderboard panels. Detailed Boss and Members sections follow. Cards are used only for distinct panels and repeated metrics; no nested cards.

## Components

- Fixed header with brand, data freshness, and section navigation.
- Metric strip for members, total power, Boss weekly total, and review count.
- Compact upload panel that supports member-only, Boss-only, or both uploads.
- Horizontal leaderboard rows with stable row height and visible rank 10.
- Data tables with sticky headers, compact rows, review tags, and action buttons.
- Native dialog for OCR review with clearly labeled fields.
- Select controls for Boss week and snapshot linkage.
- Member search and sort controls inside the Members panel.

## Motion

Motion is minimal and functional: hover color shifts, active button press, and subtle row reveal. Respect `prefers-reduced-motion`.

## Responsive

Desktop first for dense operational use, with structural breakpoints: overview becomes stacked on tablets, leaderboard rows simplify on phones, tables remain horizontally scrollable inside their panels rather than causing page overflow.
