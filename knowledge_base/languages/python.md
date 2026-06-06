# PYTHON_SCHEMA: {Key: Strict_Requirement}
# INDENT: Spaces per level | TABS: Tab allowance
# CASE: Variable/Func/Class/Constant naming convention
# TYPE: Type hinting rules | DOC: Docstring requirement
# ERROR: Exception handling guard | REWRITE: Full script output rules

INDENT: 4
TABS: Forbidden
LINE_MAX: 88
FILE_ORDER: docstring -> imports (std, 3rd, local) -> constants -> exceptions -> dataclasses -> functions -> classes -> main_guard
CASE_VAR_FUNC: snake_case
CASE_CLASS: PascalCase
CASE_CONST: UPPER_CASE_WITH_UNDERSCORES
DOC: Required for modules, public functions, classes (intent, args, returns)
IMPORTS: Absolute preferred, sorted, no wildcards, remove unused
TYPE: Hints mandatory everywhere, public APIs fully typed, explicit return types
ERROR: Specific exceptions only, bare except forbidden, try-except ValueError for user inputs, fail loud
FUNCTIONS: 5-30 lines max, max 3 parameters, pure functions preferred, no mutable default arguments
CLASSES: Modeling state+behavior only, no God objects, prefer composition, use @dataclass for containers
MUTABILITY: Return new objects, avoid input mutation
STRUCTURES: set (membership), dict (lookups), list (ordered collections)
CONTROL_FLOW: Early returns, avoid deep nesting/complex ternaries
LOGGING: Use logging module, no print, appropriate levels (DEBUG, INFO, WARNING, ERROR)
SECURITY: Validate input, avoid eval/exec, env vars for secrets
CLEANUP: Absolute nuke ban on stubs (# ...), remove dead code/comments/debug