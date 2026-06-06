# JS_TS_TSX_SCHEMA: {Key: Strict_Requirement}
# LANG: Syntax and variables | INDENT: Spaces per level
# FILE_ORDER: Structuring block order | CASE: Naming rules
# TS_RULES: TypeScript type requirements | TSX_RULES: React rendering requirements

LANG: ES6+ syntax, const by default, let only for reassignment, var forbidden
INDENT: 4 spaces, no tabs, one statement per line, curly braces mandatory for all blocks
FILE_STRUCTURE: File comment -> Constants -> Utility functions -> Core logic -> Event bindings -> Initialization
CASE_VAR_FUNC: camelCase
CASE_CLASS: PascalCase
CASE_CONST: UPPER_CASE
ERROR_HANDLING: Fail loudly, validate external input, never swallow errors, try/catch only if recovery possible, clear messages
CONTROL_FLOW: Early returns, avoid deep nesting, avoid complex ternary chains, no magic values
DATA: Immutable patterns preferred, clone objects explicitly, avoid input mutation
DOM: Query once and cache references, semantic selectors, minimize manipulation, data-* for exchange
EVENTS: Event delegation preferred, no inline handlers, clean up listeners, separate logic from events
ASYNC: async/await over promises, mandatory error catch, no unhandled rejections
PERFORMANCE: Batch DOM updates, avoid reflows, non-blocking main thread, avoid expensive loops on huge data
SECURITY: Never trust user input, eval/new Function forbidden, sanitize dynamic content, zero secrets
MODULES: ES modules preferred, one responsibility per file, no circular dependencies
TESTABILITY: Logic independent of DOM, prefer dependency injection, no hidden global state
CLEANUP: Remove dead/commented code, zero debug logs, no console.log in final output
TS_RULES: Type annotations mandatory on functions/variables, no implicit Any, use interfaces/types for state/props structure, strict null compliance
TSX_RULES: JSX/TSX elements must return semantic trees, components require explicit export statements, zero layout/styling definitions inside hooks