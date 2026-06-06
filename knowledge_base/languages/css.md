# CSS_SCHEMA: {Key: Strict_Requirement}
# ROLE: Purpose definition | LOCATION: Tag placement parameters
# INDENT: Spaces per level | ORDER: Cascade execution order

ROLE: Presentation controls only, zero business logic, no unused rules, inline styles forbidden except dynamic overrides
LOCATION: Internal <style> block in <head> default, external stylesheet only when explicitly requested, one sheet per page max
INDENT: 4 spaces, no tabs, one property per line, one selector per line, trailing semicolons mandatory, lowercase selectors/properties
ORGANIZATION: Reset/normalize -> CSS variables -> Base elements -> Layout -> Components -> Utilities -> Media queries
VARIABLES: Define in :root, mandatory for colors/spacing/fonts/radii, zero magic numbers outside variables
SELECTORS: Class selectors preferred, ID styling forbidden, layout elements forbidden, max nesting depth 3, no high specificity
NAMING: kebab-case only, purpose-based/descriptive, avoid presentational/generic names
LAYOUT: Flexbox or Grid preferred, floats forbidden, absolute positioning restricted, mobile-first, relative units (rem, %, vh, vw), zero fixed heights
RESPONSIVENESS: Mobile-first responsive by default, natural text/content reflow, no device-specific breakpoints, media queries at bottom
TYPOGRAPHY: rem-based font sizes, explicit type scale, line-height >= 1.4, avoid font weight bloating, sans-serif default
COLORS: Accessible contrast ratios, variable-driven, no hardcoded hex strings outside root variables, minimal palette
SPACING: Consistent spacing scale, margin/padding follow system grid, zero arbitrary space adjustments
EFFECTS: Subtle shadows only, no heavy blur/glow, purposeful animations using transform/opacity only
MEDIA_QUERIES: Grouped at the bottom, zero rule duplication, modify layout layouts not content meaning
PERFORMANCE: Remove unused selectors, avoid complex matching selectors, minimize repaint/reflow triggers
ACCESSIBILITY: Focus outlines mandatory, visible focus states, respect reduced-motion queries
CLEANUP: Clear commented-out rules, delete unused variables/selectors, readability > cleverness
VALIDATION: Valid CSS only, no deprecated properties, no browser hacks