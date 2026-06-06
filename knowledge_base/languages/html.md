# HTML_SCHEMA: {Key: Strict_Requirement}
# BOILERPLATE: Mandatory tags | STRUCTURE: Tag sequencing order
# INDENT: Spaces per level | SEMANTICS: Tag choice preferences
# A11Y: Accessibility requirements | CSS_LOC: CSS block configuration

BOILERPLATE: doctype, html lang="en", meta charset="UTF-8", meta viewport, title mandatory
FILE_STRUCTURE: doctype -> html -> head (meta, styles) -> body -> layout elements -> scripts at end
INDENT: 4 spaces, no tabs, strict parent-child hierarchy
ATTRIBUTES: One per line if attributes > 2, lowercase tags and attributes, close all non-void tags explicitly
SEMANTICS: Use semantic elements (<header>, <nav>, <main>, <section>, <article>, <aside>, <footer>) by default. Avoid <div> unless no alternative. Headings sequential, exactly one <h1> per page.
A11Y: alt attributes mandatory for img, inputs require associated <label>, use <button> (not clickable <div>), keyboard-accessible, sufficient contrast
CSS_LOCATION: Internal <style> inside <head> by default. External only if explicitly requested. Inline styles forbidden except dynamic overrides.
CSS_ORGANIZATION: Reset/base -> Layout -> Component -> Utility. Group related rules, avoid redundant selectors.
CSS_NAMING: Class-based, kebab-case, purpose-based, not visual. No styling by ID. Avoid overly generic names.
LAYOUT: Flexbox/Grid preferred, no floats, responsive, mobile-first, relative units (rem, %, vh, vw) over px, avoid fixed widths
STYLING: Sans-serif default, minimal aesthetic, subtle shadows only, sparingly rounded corners, neutral palette, consistent spacing, no visual clutter
RESPONSIVENESS: Mobile, tablet, desktop support. Reflow naturally. No device-specific breakpoints.
PERFORMANCE: Avoid deep DOM depth/nesting, minimize selector complexity, no unused rules, no large inline assets, correct img resolution
FORMS: Semantic form elements, HTML attribute validation, correct type, <fieldset> and <legend> for groups, user-friendly errors
JS_INTERACTION: Functional without JS fallback, JS must not define layout/style, data-* for hooks, no inline event handlers
MEDIA: <img> for source, <picture> for responsive, define width/height/aspect-ratio, lazy-load when appropriate, no background img for critical text
SECURITY: No inline scripts, prevent user HTML injection, sanitize dynamic content, zero secrets in markup
CLEANUP: Remove unused elements, remove commented markup/unused CSS, zero placeholder content in final output
VALIDATION: Valid HTML5, no deprecated tags, no invalid nesting, zero duplicate IDs