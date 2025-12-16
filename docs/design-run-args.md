# `blq run` Argument Parameterization

## Status: Draft

## Overview

This document specifies how registered commands can accept arguments, allowing
commands to be parameterized at runtime rather than being static strings.

## Placeholder Syntax

| Syntax | Mode | Filled by position? | Filled by keyword? |
|--------|------|---------------------|-------------------|
| `{name}` | Keyword-only, required | No | Yes |
| `{name=default}` | Keyword-only, optional | No | Yes |
| `{name:}` | Positional-able, required | Yes | Yes |
| `{name:=default}` | Positional-able, optional | Yes | Yes |

### Design Rationale

- **Keyword-only by default** (`{name=default}`) is safer - prevents accidental
  positional filling
- **Opt-in to positional** with `:` prefix on the `=` (or just `:` for required)
- Syntax is visually distinct and easy to parse

## Argument Parsing Rules

1. **Named args** (`key=value`) fill their specific placeholder
2. **Positional args** fill positional-able placeholders left-to-right
3. **Extra positional args** (after all positional-able placeholders filled) are
   appended to command
4. **`::`** explicitly marks "everything after is passthrough"
5. **`-aN`** flag says "use exactly N positional args for placeholders"

## Examples

### Example 1: All Keyword-Only

```yaml
build:
  cmd: "make -j{jobs=4} {target=all}"
```

```bash
blq run build                      → make -j4 all
blq run build jobs=8               → make -j8 all
blq run build target=clean         → make -j4 clean
blq run build jobs=8 target=clean  → make -j8 clean
blq run build clean                → make -j4 all clean      # passthrough
blq run build jobs=8 clean test    → make -j8 all clean test # passthrough
```

### Example 2: Mixed Modes

```yaml
test:
  cmd: "pytest {path:=tests/} -v --timeout={timeout=30}"
```

```bash
blq run test                       → pytest tests/ -v --timeout=30
blq run test unit/                 → pytest unit/ -v --timeout=30
blq run test unit/ timeout=60      → pytest unit/ -v --timeout=60
blq run test path=unit/ timeout=60 → pytest unit/ -v --timeout=60
blq run test unit/ -k foo          → pytest unit/ -v --timeout=30 -k foo
```

### Example 3: Multiple Positional

```yaml
deploy:
  cmd: "kubectl apply -f {file:} -n {namespace:=default}"
```

```bash
blq run deploy manifest.yaml           → kubectl apply -f manifest.yaml -n default
blq run deploy manifest.yaml prod      → kubectl apply -f manifest.yaml -n prod
blq run deploy file=app.yaml           → kubectl apply -f app.yaml -n default
blq run deploy manifest.yaml namespace=prod → kubectl apply -f manifest.yaml -n prod
blq run deploy                         → ERROR: Missing required argument 'file'
```

### Example 4: Passthrough Control

```yaml
docker:
  cmd: "docker {command:=run} {image:}"
```

```bash
blq run docker build myimage           → docker build myimage
blq run docker myimage                 → docker run myimage
blq run docker myimage -it /bin/bash   → docker run myimage -it /bin/bash
blq run docker :: --help               → docker run --help  # skip both placeholders
blq run docker -a1 myimage --help      → docker myimage --help  # only 1 positional
```

## Error Messages

```bash
$ blq run deploy
ERROR: Missing required argument 'file'

Usage: blq run deploy <file> [namespace=default]

Arguments:
  file        (required)
  namespace   (default: default)

$ blq run deploy --help
deploy: kubectl apply -f {file:} -n {namespace:=default}

Arguments:
  file        Positional or keyword, required
  namespace   Positional or keyword, default: default

Examples:
  blq run deploy manifest.yaml
  blq run deploy manifest.yaml prod
  blq run deploy file=manifest.yaml namespace=prod
```

## MCP Interface

```json
{
  "command": "deploy",
  "args": {
    "file": "manifest.yaml",
    "namespace": "prod"
  },
  "extra": ["--dry-run"]
}
```

All args are passed by name in MCP. The `extra` array handles passthrough.

## commands.yaml Schema

```yaml
commands:
  build:
    cmd: "make -j{jobs=4} {target=all}"
    description: "Build the project"
    timeout: 300
    capture: true

  test:
    cmd: "pytest {path:=tests/} -v"
    description: "Run tests"
    timeout: 600

  deploy:
    cmd: "kubectl apply -f {file:} -n {namespace:=default}"
    description: "Deploy to Kubernetes"
    timeout: 120
```

## Implementation Notes

### Placeholder Parsing

Parse placeholders from command template using regex:
```
\{([a-zA-Z_][a-zA-Z0-9_]*)(:(=([^}]*))?|=([^}]*))?\}
```

This captures:
- Group 1: name
- Group 2: `:` or `:=...` or `=...`
- Groups 3-5: default value variants

### Placeholder Data Structure

```python
@dataclass
class CommandPlaceholder:
    name: str
    default: str | None  # None = required
    positional: bool     # Can be filled positionally
```

### Command Expansion

```python
def expand_command(
    template: str,
    placeholders: list[CommandPlaceholder],
    named_args: dict[str, str],
    positional_args: list[str],
    extra_args: list[str],
) -> str:
    ...
```

## Future Extensions (Phase 2)

Reserved for later if needed:

- `{/name}` - Positional-only (can't use keyword)
- `{=NAME=default}` - Inline literal (`NAME=value` in output)
- `{*name=default}` - Alternative keyword-only syntax (Python-style)
